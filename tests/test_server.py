"""HTTP-контракт проверяется с реальной корзиной и без обращений к OpenAI."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from agent.catalog import Catalog
from agent.tools import Toolbox
from server import create_app

SKU = 'EKF-MCB4763-1-25C'


class FakeAgent:
    def __init__(self, catalog, cart):
        self.tools = Toolbox(catalog, cart)
        self.messages = []

    def ask(self, text):
        cart = self.tools.cart
        cart.turn += 1
        self.tools.last_user_message = text
        if text == 'error':
            raise RuntimeError('private secret')
        if text.startswith('предложи'):
            proposal = self.tools.propose_add_to_cart(SKU, 2)
            if text.endswith('и сразу да'):
                result = self.tools.confirm_add_to_cart(proposal['proposal_id'])
                assert not result['ok']
            return 'Добавить товар?'
        if text in ('да', 'иә', 'нет'):
            result = self.tools.confirm_add_to_cart(next(iter(cart.pending)))
            return 'Добавлено' if result['ok'] else result['error']
        return f'Ответ: {text}'


@pytest.fixture
def client():
    products = json.loads((Path(__file__).resolve().parents[1] / 'data/catalog_sample.json').read_text(encoding='utf-8'))
    return TestClient(create_app(Catalog(products), FakeAgent))


def propose(client, session_id=None):
    response = client.post('/chat', json={'message':'предложи и сразу да', 'session_id': session_id})
    assert response.status_code == 200
    data = response.json()
    assert data['pending']
    return data


def test_sessions_agents_and_carts_are_isolated(client):
    first, second = propose(client), propose(client)
    assert first['session_id'] != second['session_id']
    assert first['cart']['cart_id'] != second['cart']['cart_id']
    assert first['cart']['items'] == []
    assert second['cart']['items'] == []
    confirmed = client.post('/chat', json={'session_id':first['session_id'], 'message':'да'}).json()
    assert confirmed['cart']['items'][0]['qty'] == 2
    restored = client.get('/session/' + second['session_id']).json()
    assert restored['cart']['items'] == []
    assert len(restored['history']) == 2


@pytest.mark.parametrize('confirmation', ['да', 'иә'])
def test_separate_text_confirmation(client, confirmation):
    data = propose(client)
    assert data['cart']['items'] == []
    response = client.post('/chat', json={'session_id':data['session_id'], 'message':confirmation})
    assert response.json()['cart']['items'][0]['qty'] == 2


def test_button_confirmation_and_live_cart_link(client):
    data = propose(client)
    url = data['cart_url']
    assert url.startswith('http://testserver/cart/')
    assert 'Корзина пуста' in client.get(url).text
    body = {'session_id':data['session_id'], 'message':'Да, добавь', 'proposal_id':data['pending'][0]['proposal_id']}
    response = client.post('/chat', json=body)
    assert response.status_code == 200
    assert response.json()['pending'] == []
    page = client.get(url)
    assert SKU in page.text and '2760' in page.text
    assert page.headers['cache-control'] == 'no-store'
    assert client.post('/chat', json=body).status_code == 409
    assert client.get('/session/'+data['session_id']).json()['cart']['items'][0]['qty'] == 2
    second = propose(client, data['session_id'])
    client.post('/chat', json={'session_id':data['session_id'], 'message':'да', 'proposal_id':second['pending'][0]['proposal_id']})
    assert '5520' in client.get(url).text


@pytest.mark.parametrize('message', ['нет', 'жоқ, қоспаңыз', 'Какая гарантия?'])
def test_button_field_does_not_bypass_confirmation(client, message):
    data = propose(client)
    response = client.post('/chat', json={'session_id':data['session_id'], 'message':message, 'proposal_id':data['pending'][0]['proposal_id']})
    assert response.json()['cart']['items'] == []
    assert response.json()['pending']


def test_cannot_confirm_another_session_proposal(client):
    first, second = propose(client), propose(client)
    response = client.post('/chat', json={'session_id':second['session_id'], 'message':'да', 'proposal_id':first['pending'][0]['proposal_id']})
    assert response.status_code == 409
    assert client.get('/session/'+first['session_id']).json()['cart']['items'] == []


def test_concurrent_duplicate_confirmation_adds_once(client):
    data = propose(client)
    body = {'session_id':data['session_id'], 'message':'да', 'proposal_id':data['pending'][0]['proposal_id']}
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: client.post('/chat', json=body).status_code, range(2)))
    assert sorted(statuses) == [200,409]
    assert client.get('/session/'+data['session_id']).json()['cart']['items'][0]['qty'] == 2


def test_validation_unknown_sessions_and_error_recovery(client):
    for message in ['', '   ', 'a'*8001]:
        assert client.post('/chat', json={'message':message}).status_code == 422
    assert client.post('/chat', json={'message':'да', 'session_id':'a'*32}).status_code == 404
    assert client.post('/chat', json={'message':'да', 'proposal_id':'fake'}).status_code == 400
    assert client.get('/cart/unknown').status_code == 404
    data = client.post('/chat', json={'message':'error'}).json()
    assert 'private' not in json.dumps(data)
    assert client.post('/chat', json={'message':'привет', 'session_id':data['session_id']}).json()['answer'] == 'Ответ: привет'


def test_html_escaping_and_utf8_assets():
    products = [{'sku':SKU,'name':'<script>alert(1)</script>','brand':'','category':'','specs':{},'stock':{'Склад':5},'price_kzt':10,'unit':'шт','min_order_qty':1,'certificates':[]}]
    client = TestClient(create_app(Catalog(products),FakeAgent))
    data = propose(client)
    client.post('/chat',json={'session_id':data['session_id'],'message':'да'})
    page=client.get(data['cart_url'])
    assert '<script>alert(1)</script>' not in page.text
    assert '&lt;script&gt;' in page.text
    assert 'charset=utf-8' in page.headers['content-type']
    assert 'Ваше сообщение' in client.get('/widget.html').text
    assert 'attachShadow' in client.get('/widget.js').text


def test_button_rechecks_stock(client):
    # Меняем остаток после предложения: сервер должен вызвать реальную проверку Cart.confirm.
    product = {'sku':SKU,'name':'Автомат','brand':'','category':'','specs':{},
               'stock':{'Склад':2},'price_kzt':10,'unit':'шт','min_order_qty':1,'certificates':[]}
    local = TestClient(create_app(Catalog([product]),FakeAgent))
    data = propose(local)
    product['stock']['Склад'] = 1
    response = local.post('/chat',json={'session_id':data['session_id'],'message':'да',
                                      'proposal_id':data['pending'][0]['proposal_id']})
    assert response.json()['cart']['items'] == []
    assert 'Остаток изменился' in response.json()['answer']
