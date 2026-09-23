"""Регрессии реальных карточек и отказов API; без внешних запросов."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import requests

from agent.catalog import Catalog, normalize_api_product, electrical_values, product_kind
from agent.cart import Cart
from agent.tools import Toolbox

DATA = Path(__file__).resolve().parents[1] / 'data'


def real_catalog():
    return Catalog([normalize_api_product(p) for p in json.loads((Path(__file__).parent / 'fixtures' / 'catalog_live.json').read_text())])


def test_real_iek_query_respects_brand_current_poles():
    found = real_catalog().search('Есть ли в наличии автомат IEK 25А 1P?')
    assert any(p['id'] == 21450 for p in found)
    for p in found:
        assert 'IEK' in p['name']
        assert electrical_values(p)['current'] == 25
        assert electrical_values(p)['poles'] == '1'
    assert real_catalog().search('автомат IEK 999А 1P') == []


def test_real_categories_and_cable_specs():
    catalog = real_catalog()
    for kind in ['автомат', 'узо', 'кабель', 'розетка', 'светильник']:
        assert catalog.search(kind)
        assert all(product_kind(p) == kind for p in catalog.search(kind))
    assert any('Сечение, мм²' in p['specs'] for p in catalog.products if product_kind(p) == 'кабель')


def test_real_analogs_do_not_change_current_or_poles():
    catalog = real_catalog()
    tool = Toolbox(catalog, Cart(catalog))
    base = catalog.get('11006DEK')
    analogs = tool.find_analogs(base['sku'])['analogs']
    assert analogs
    for item in analogs:
        values = electrical_values(catalog.get(item['sku']))
        assert values['current'] == electrical_values(base)['current']
        assert values['poles'] == electrical_values(base)['poles']
        assert item['matching_specs']
        assert 'differences' in item and 'price_diff_kzt' in item


def test_timeout_uses_cached_detail(tmp_path, monkeypatch, caplog):
    from agent import catalog
    raw = json.loads((DATA / 'api_sample_detail.json').read_text())
    full = normalize_api_product(raw)
    (tmp_path / 'catalog_cache.json').write_text(json.dumps([full]))
    monkeypatch.setattr(catalog, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(catalog, '_api_get', Mock(side_effect=requests.Timeout('secret should not be logged')))
    partial = normalize_api_product({k: raw[k] for k in ('id','article','name','price')})
    result = Catalog([partial]).get(raw['article'])
    assert result['stock'] == full['stock']
    assert 'кэша' in result['data_warning']
    assert 'Timeout' in caplog.text
    assert 'secret' not in caplog.text


def test_tool_errors_logged_without_sensitive_exception(caplog, monkeypatch):
    catalog = Catalog([])
    tools = Toolbox(catalog, Cart(catalog))
    monkeypatch.setattr(catalog, 'search', Mock(side_effect=ValueError('private credential')))
    result = json.loads(tools.call('search_products', '{"query":"автомат"}'))
    assert 'error' in result
    assert 'search_products' in caplog.text and 'ValueError' in caplog.text
    assert 'private credential' not in caplog.text + json.dumps(result)


def test_eighth_step_is_final_response(monkeypatch):
    from agent import agent as module
    client = Mock()
    monkeypatch.setattr(module, 'OpenAI', lambda: client)
    tool_call = SimpleNamespace(id='call', function=SimpleNamespace(name='search_products', arguments='{"query":"missing"}'))
    tool_msg = SimpleNamespace(tool_calls=[tool_call], content=None,
                               model_dump=lambda **kw: {'role':'assistant','content':None,'tool_calls':[]})
    final_msg = SimpleNamespace(tool_calls=[], content='В локальной выборке не найдено',
                                model_dump=lambda **kw: {'role':'assistant','content':'В локальной выборке не найдено'})
    client.chat.completions.create.side_effect = [SimpleNamespace(choices=[SimpleNamespace(message=tool_msg)])]*7 + [SimpleNamespace(choices=[SimpleNamespace(message=final_msg)])]
    catalog = Catalog([])
    agent = module.ShopAgent(catalog, Cart(catalog))
    assert agent.max_steps == 8
    assert agent.ask('автомат') == 'В локальной выборке не найдено'
    assert client.chat.completions.create.call_args.kwargs['tool_choice'] == 'none'


def test_model_timeout_returns_local_data(monkeypatch):
    from openai import APITimeoutError
    from agent import agent as module
    client=Mock()
    monkeypatch.setattr(module, 'OpenAI', lambda: client)
    client.chat.completions.create.side_effect=APITimeoutError(request=Mock())
    catalog=real_catalog()
    agent=module.ShopAgent(catalog,Cart(catalog))
    answer=agent.ask('автомат IEK 25А 1P')
    assert 'ВА47-29' in answer and 'кэша' in answer
