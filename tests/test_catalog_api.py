"""API-контракт по сохранённым реальным ответам, без сети и секретов."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent.catalog import Catalog, load_from_api, normalize_api_product, total_stock

DATA = Path(__file__).resolve().parents[1] / 'data'


def sample(name):
    return json.loads((DATA / name).read_text(encoding='utf-8'))


def test_real_detail_fields():
    product = normalize_api_product(sample('api_sample_detail.json'))
    assert product['sku'] == '200300285_'
    assert product['brand'] == 'Legrand'
    assert product['price_kzt'] == 64920
    assert total_stock(product) == 23
    assert product['stock']['Алматы'] == 5
    assert product['min_order_qty'] == 1
    assert product['category'] == 'Автоматический выключатель'
    # Противоречие в источнике сохраняем, не исправляем догадкой по названию.
    assert product['specs']['Номинальный ток'] == '250 А'
    assert '160А' in product['name']
    assert product['certificates'] == []
    assert product['unit'] == ''
    assert not product['_detail_pending']


def test_zero_price_and_quantity_fallback():
    raw = sample('api_sample_detail.json')
    raw.update(price=0, stores=[], quantity=0)
    product = normalize_api_product(raw)
    assert product['price_kzt'] == 0
    assert product['stock'] == {'Общий остаток': 0}


def test_pagination_auth_and_empty_last_page(monkeypatch):
    import requests

    first = sample('api_sample_list.json')
    second = {'page': 2, 'per_page': 20, 'count': 1,
              'items': [sample('api_sample_detail.json')]}
    get = Mock(side_effect=[Mock(json=lambda: first), Mock(json=lambda: second)])
    monkeypatch.setattr(requests, 'get', get)
    monkeypatch.setenv('EKT_API_USER', 'test-user')
    monkeypatch.setenv('EKT_API_PASSWORD', 'test-password')
    monkeypatch.setenv('EKT_API_BASE', 'https://ekt.kz/api/')
    products = load_from_api()
    assert len(products) == 21
    assert products[0]['_detail_pending']
    assert [c.kwargs['params'] for c in get.call_args_list] == [{'page': 1}, {'page': 2}]
    for call in get.call_args_list:
        assert call.args[0] == 'https://ekt.kz/api/products'
        assert call.kwargs['auth'] == ('test-user', 'test-password')


@pytest.mark.parametrize('last', [{'items': []}, None])
def test_empty_or_repeated_page_stops(monkeypatch, last):
    first = sample('api_sample_list.json')
    get = Mock(side_effect=[first, first if last is None else last])
    monkeypatch.setattr('agent.catalog._api_get', get)
    assert len(load_from_api()) == 20
    assert get.call_count == 2


def test_found_product_loads_detail_once(monkeypatch):
    detail = sample('api_sample_detail.json')
    listing = {k: detail[k] for k in ('id', 'article', 'name', 'price', 'url')}
    get = Mock(return_value=detail)
    monkeypatch.setattr('agent.catalog._api_get', get)
    cat = Catalog([normalize_api_product(listing)])
    assert get.call_count == 0
    found = cat.search('DRX250')
    assert total_stock(found[0]) == 23
    assert cat.get('200300285_') is found[0]
    get.assert_called_once_with('products/detail', {'id': 515291})


def test_detail_failure_does_not_cache_or_fake_stock(monkeypatch):
    raw = sample('api_sample_list.json')['items'][0]
    product = normalize_api_product(raw)
    get = Mock(side_effect=RuntimeError('API unavailable'))
    monkeypatch.setattr('agent.catalog._api_get', get)
    cat = Catalog([product])
    with pytest.raises(RuntimeError):
        cat.get(product['sku'])
    assert product['_detail_pending']
    assert product['stock'] == {}


def test_analogs_hydrate_candidates(monkeypatch):
    base = sample('api_sample_detail.json')
    other = copy.deepcopy(base)
    base['quantity'] = 0
    base['stores'] = []
    other.update(id=123, article='analog', price=60000)
    raws = [base, other]
    get = Mock(side_effect=lambda path, params: next(r for r in raws if r['id'] == params['id']))
    monkeypatch.setattr('agent.catalog._api_get', get)
    cat = Catalog([normalize_api_product({k: r[k] for k in ('id', 'article', 'name', 'price')}) for r in raws])
    analogs = cat.analogs(base['article'])
    assert analogs[0]['product']['sku'] == 'analog'
    assert analogs[0]['matching_specs']
    assert analogs[0]['price_diff_kzt'] == -4920
