"""Сопутствующие товары, сводка менеджеру и казахский язык без сети."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openai import APITimeoutError

from agent.cart import Cart, is_explicit_confirmation
from agent.catalog import Catalog
from agent.language import detect_language
from agent.tools import TOOL_SCHEMAS, Toolbox

DATA = Path(__file__).resolve().parents[1] / "data"
BREAKER = "IEK-MVA20-1-016-C"
CABLE = "KAB-VVGNG-LS-3X2.5"


@pytest.fixture
def catalog():
    return Catalog(json.loads((DATA / "catalog_sample.json").read_text(encoding="utf-8")))


def accessory(sku, name, stock=10, minimum=1):
    return {"sku": sku, "name": name, "category": "Комплектующие", "brand": "Тест",
            "price_kzt": 500, "unit": "шт", "min_order_qty": minimum,
            "stock": {"Тестовый склад": stock}, "specs": {}, "certificates": []}


@pytest.fixture
def toolbox(catalog):
    products = catalog.products + [
        accessory("DIN-EMPTY", "DIN-рейка 35 мм", stock=0),
        accessory("DIN-1", "DIN-рейка 35 мм"),
        accessory("BOX-1", "Бокс на 4 модуля"),
        accessory("PIPE-SHORT", "Гофра ПВХ", stock=2, minimum=10),
        accessory("PIPE-1", "Труба гофрированная ПВХ"),
        accessory("NOT-RAIL", "Розетка на DIN-рейку"),
    ]
    catalog = Catalog(products)
    return Toolbox(catalog, Cart(catalog))


@pytest.mark.parametrize(("sku", "expected"), [
    (BREAKER, {"din_rail": ["DIN-1"], "box": ["BOX-1"]}),
    (CABLE, {"conduit": ["PIPE-1"]}),
])
def test_accessories_come_from_catalog_without_changing_cart(toolbox, sku, expected):
    result = json.loads(toolbox.call("recommend_accessories", json.dumps({"sku": sku})))
    groups = result["recommendations"]
    assert {g["kind"]: [p["sku"] for p in g["products"]] for g in groups} == expected
    for group in groups:
        assert group["reason"] and group["compatibility"] == "requires_check"
        for p in group["products"]:
            assert p["price_kzt"] == toolbox.catalog.get(p["sku"])["price_kzt"]
            assert p["in_stock"] > 0
    assert toolbox.cart.items == {} and toolbox.cart.pending == {}


def test_missing_accessories_are_categories_without_invented_products(catalog):
    tools = Toolbox(catalog, Cart(catalog))
    result = tools.recommend_accessories(BREAKER)
    assert [g["kind"] for g in result["recommendations"]] == ["din_rail", "box"]
    assert all(g["products"] == [] for g in result["recommendations"])
    assert "локальной выборке" in result["note"]
    assert tools.recommend_accessories("SE-ATN000143")["recommendations"] == []
    assert "error" in tools.recommend_accessories("unknown")


def test_accessory_detail_is_loaded_before_checking_stock(catalog, monkeypatch):
    from agent.catalog import normalize_api_product

    raw = {"id": 99, "article": "DIN-API", "name": "DIN-рейка", "price": 700}
    catalog = Catalog(catalog.products + [normalize_api_product(raw)])
    fetch = Mock(return_value={**raw, "quantity": 5, "properties": {"OBYEM": "DIN-рейка"}})
    monkeypatch.setattr("agent.catalog._api_get", fetch)
    groups = catalog.accessories(BREAKER)
    assert groups[0]["products"][0]["stock"] == {"Общий остаток": 5}
    fetch.assert_called_once_with("products/detail", {"id": 99})


def test_accessory_types_do_not_confuse_base_products(catalog):
    catalog = Catalog(catalog.products + [accessory("BOX", "Бокс для автоматов"),
                                          accessory("CABLE", "Гофрированный кабель")])
    assert catalog.accessories("BOX") == []
    assert catalog.accessories(CABLE) == [{"kind": "conduit", "products": []}]


def test_recommended_accessory_still_requires_confirmation(toolbox):
    toolbox.recommend_accessories(BREAKER)
    proposal = toolbox.propose_add_to_cart("DIN-1", 1)
    toolbox.last_user_message = "Иә, қосыңыз"
    assert not toolbox.confirm_add_to_cart(proposal["proposal_id"])["ok"]
    toolbox.cart.turn += 1
    assert toolbox.confirm_add_to_cart(proposal["proposal_id"])["ok"]
    assert toolbox.cart.items == {"DIN-1": 1}


def test_manager_summary_preserves_context_and_pending_is_not_an_order(toolbox):
    toolbox.record_message("user", "Нужен автомат 16А, доставка в Алматы")
    toolbox.get_product(BREAKER)
    toolbox.record_message("assistant", "Нашёл автомат; уточните количество.")
    proposal = toolbox.propose_add_to_cart(BREAKER, 2)
    result = json.loads(toolbox.call("request_manager", '{"reason":"Нужна консультация по монтажу"}'))
    assert result["status"] == "prepared"
    assert "Алматы" in result["summary"] and BREAKER in result["summary"]
    assert "Подтверждённая корзина:\nПуста." in result["summary"]
    assert "НЕ подтверждённые" in result["summary"]
    assert "Автоматическая отправка не подключена" in result["note"]
    toolbox.cart.turn += 1
    toolbox.last_user_message = "Да, добавь"
    toolbox.confirm_add_to_cart(proposal["proposal_id"])
    refreshed = toolbox.request_manager()
    assert refreshed["request_id"] == result["request_id"]
    assert "НЕ подтверждённые" not in refreshed["summary"]
    assert f"{BREAKER}:" in refreshed["summary"]


@pytest.mark.parametrize("security_code", ["CVV: 123", "CVC — 123", "CVV:\n123"])
def test_manager_summary_redacts_payment_details_and_includes_attachment(toolbox, security_code):
    toolbox.record_message("user", f"Карта 4111 1111 1111 1111, {security_code}",
                           attachment_context=f"Спецификация: {CABLE}, 20 м")
    result = toolbox.request_manager()
    stored = json.dumps(toolbox.dialogue, ensure_ascii=False) + result["summary"]
    assert "4111" not in stored and "123" not in stored
    assert CABLE in result["summary"] and "Проверка вложений" in result["summary"]


def test_empty_manager_request_is_session_local(catalog):
    first = Toolbox(catalog, Cart(catalog))
    second = Toolbox(catalog, Cart(catalog))
    assert "ещё не описал" in first.request_manager()["summary"]
    assert second.manager_request is None
    assert first.cart.items == {} and second.cart.items == {}


@pytest.mark.parametrize(("text", "previous", "expected"), [
    ("Маған автомат керек", "ru", "kk"), ("Автомат 25А бар ма?", "ru", "kk"),
    (BREAKER, "kk", "kk"), ("2", "kk", "kk"),
    ("Покажите автомат", "kk", "ru"), ("Орысша жауап беріңіз", "kk", "ru"),
    ("Какие условия покупки?", "kk", "ru"),
    ("Ответь на казахском", "ru", "kk"),
])
def test_language_detection_and_continuity(text, previous, expected):
    assert detect_language(text, previous) == expected


@pytest.mark.parametrize("text", ["Қоспаңыз", "Иә, бірақ қоспа", "Қажет емес", "Растамаймын",
                                  "Қосуға бола ма?", "Қос полюсті автомат бар ма?"])
def test_kazakh_refusal_or_question_cannot_confirm_cart(toolbox, text):
    proposal = toolbox.propose_add_to_cart(BREAKER, 1)
    toolbox.cart.turn += 1
    toolbox.last_user_message = text
    assert not is_explicit_confirmation(text)
    assert not toolbox.confirm_add_to_cart(proposal["proposal_id"])["ok"]
    assert toolbox.cart.items == {}


@pytest.mark.parametrize("text", ["Иә, қосыңыз", "Растаймын", "Себетке қос"])
def test_kazakh_explicit_confirmation(text):
    assert is_explicit_confirmation(text)


def test_kazakh_replies_prompt_fallback_and_manager_summary(catalog, monkeypatch):
    from agent import agent as module

    client = Mock()
    monkeypatch.setattr(module, "OpenAI", lambda: client)
    client.chat.completions.create.side_effect = APITimeoutError(request=Mock())
    shop = module.ShopAgent(catalog, Cart(catalog))
    answer = shop.ask("Маған 16А автомат керек")
    assert shop.language == "kk" and "қалдық" in answer and BREAKER in answer
    assert "Жауап тілі: қазақша" in shop.messages[0]["content"]
    shop.ask("UNKNOWN-999")
    assert shop.language == "kk" and "табылмады" in shop.tools.dialogue[-1]["content"]
    result = shop.tools.request_manager()
    assert "Клиент тілі: қазақша" in result["summary"]
    assert "Менеджермен байланысу" in result["contact"]
    assert "жергілікті каталогта" in shop.tools.recommend_accessories(BREAKER)["note"]


def test_explicit_language_selection_overrides_message_language(catalog, monkeypatch):
    from agent import agent as module

    client = Mock()
    reply = SimpleNamespace(tool_calls=[], content="Көмектесемін.",
                            model_dump=lambda **kwargs: {"role": "assistant", "content": "Көмектесемін."})
    client.chat.completions.create.return_value.choices = [SimpleNamespace(message=reply)]
    monkeypatch.setattr(module, "OpenAI", lambda: client)
    shop = module.ShopAgent(catalog, Cart(catalog))
    assert shop.ask("Покажите автомат", language="kk") == "Көмектесемін."
    assert shop.language == "kk"
    assert shop.tools.dialogue[-1]["content"] == "Көмектесемін."
    assert {"recommend_accessories", "request_manager"} <= {s["function"]["name"] for s in TOOL_SCHEMAS}
