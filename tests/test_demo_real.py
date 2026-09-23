"""Шаги демонстрации на реальном кэше; без внешних запросов и синтетических товаров."""
import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from agent import catalog as module
from agent.agent import ShopAgent
from agent.attachments import extract_text, specification_context
from agent.cart import Cart
from agent.tools import Toolbox
from server import create_app

ROOT = Path(__file__).resolve().parents[1]


def real():
    return module.Catalog(json.loads((ROOT/'data/catalog_cache.json').read_text(encoding='utf-8')))


def test_demo_steps_with_real_tools():
    c=real(); box=Toolbox(c,Cart(c))
    assert any(p['sku']=='010500008_' for p in box.search_products('автомат IEK 25А 1P')['results'])
    assert box.get_product('010500008_')['in_stock'] > 0
    assert box.get_product('11006DEK')['in_stock'] == 0
    analogs=box.find_analogs('11006DEK')['analogs']
    assert analogs and all(a['matching_specs'] and a['differences'] for a in analogs)
    card=box.get_product('010500008_')
    assert card['certificates']==[]
    assert card['certificate_note']=='В базе нет сертификата на этот товар, могу передать запрос менеджеру'
    terms=box.get_purchase_terms('delivery')
    assert terms['source']=='https://ekt.kz/checkout-delivery/' and '30 000' in terms['delivery']
    assert terms['warnings']
    proposal=box.propose_add_to_cart('010500008_',2)
    box.last_user_message='да'
    assert not box.confirm_add_to_cart(proposal['proposal_id'])['ok']
    assert box.cart.summary()['items']==[]
    box.cart.turn+=1; box.last_user_message='Иә, қосыңыз'
    result=box.confirm_add_to_cart(proposal['proposal_id'])
    assert result['ok'] and result['cart']['total_kzt']==1616
    assert box.cart.id in result['cart_url']
    box.language='kk'
    assert box.request_manager()['language']=='kk'
    wb=Workbook();wb.active.append(['Артикул','Наименование'])
    for sku in ['010500008_','11006DEK']:
        wb.active.append([sku,c.get(sku)['name']])
    stream=io.BytesIO();wb.save(stream)
    context=specification_context(extract_text('demo.xlsx',stream.getvalue()),c)
    assert '010500008_' in context and '11006DEK' in context and 'аналоги' in context


def test_certificate_and_manager_http_without_model(monkeypatch):
    client_model=Mock()
    monkeypatch.setattr('agent.agent.OpenAI',lambda:client_model)
    client=TestClient(create_app(real(),ShopAgent))
    result=client.post('/chat',json={'message':'Покажите сертификат на 010500008_'}).json()
    assert 'В базе нет сертификата на этот товар, могу передать запрос менеджеру' in result['answer']
    assert 'Позвать менеджера' in result['answer']
    answer=client.post('/chat',json={'message':'Позвать менеджера','session_id':result['session_id']}).json()
    assert 'Сводка готова' in answer['answer'] and '010500008_' in answer['answer']
    assert 'Автоматическая отправка не подключена' in answer['answer']
    client_model.chat.completions.create.assert_not_called()
    assert 'id="manager"' in client.get('/widget.html').text


def test_default_cache_never_uses_sample(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'DATA_DIR',tmp_path)
    monkeypatch.delenv('CATALOG_SOURCE',raising=False)
    (tmp_path/'catalog_sample.json').write_text('[{"synthetic":true}]',encoding='utf-8')
    with pytest.raises(FileNotFoundError):module.load_catalog()
    monkeypatch.setenv('CATALOG_SOURCE','sample')
    assert module.load_catalog()==[{'synthetic':True}]
    monkeypatch.setenv('CATALOG_SOURCE','wrong')
    with pytest.raises(ValueError):module.load_catalog()


def test_api_falls_back_only_to_real_cache(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'DATA_DIR',tmp_path)
    monkeypatch.setenv('CATALOG_SOURCE','api')
    monkeypatch.setattr(module,'load_from_api',Mock(side_effect=TimeoutError()))
    with pytest.raises(RuntimeError):module.load_catalog()
    products=real().products[:1]
    (tmp_path/'catalog_cache.json').write_text(json.dumps(products),encoding='utf-8')
    assert module.load_catalog()==products
    monkeypatch.setattr(module,'load_from_api',lambda:products[0:1]+products[0:1])
    assert len(module.load_catalog())==2  # api выбран явно даже при наличии кэша
