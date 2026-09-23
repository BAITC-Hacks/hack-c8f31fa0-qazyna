"""Тесты по пунктам Must have из кейса ekt.kz (работают без OpenAI)."""
from agent.cart import Cart, is_explicit_confirmation
from agent.catalog import Catalog
from agent.tools import Toolbox

cat = Catalog()


def box():
    return Toolbox(cat, Cart(cat))


# 1) наличие + характеристики + сертификат
def test_product_info_with_certificate():
    p = box().get_product("EKF-ELCB-2-25-30")
    assert p["in_stock"] > 0 and p["specs"]["Ток утечки, мА"] == 30
    assert p["certificates"][0]["url"].startswith("https://")


# 2) нет в наличии → минимум один аналог с обоснованием
def test_analog_for_out_of_stock():
    assert box().get_product("IEK-MVA20-1-025-C")["in_stock"] == 0
    analogs = box().find_analogs("IEK-MVA20-1-025-C")["analogs"]
    assert analogs and analogs[0]["in_stock"] > 0 and analogs[0]["matching_specs"]


# 3) условия покупки
def test_purchase_terms():
    assert "Доставка" in box().get_purchase_terms("delivery")["delivery"]


# 4) без явного «да» корзина не меняется; количество ≤ остатка
def test_cart_requires_confirmation():
    b = box()
    prop = b.propose_add_to_cart("EKF-MCB4763-1-25C", 5)
    assert prop["ok"] and b.view_cart()["items"] == []
    b.last_user_message = "Да, добавь"  # в том же ходе, что и предложение — нельзя
    assert not b.confirm_add_to_cart(prop["proposal_id"])["ok"]
    b.cart.turn += 1
    b.last_user_message = "а какая гарантия?"
    assert not b.confirm_add_to_cart(prop["proposal_id"])["ok"]
    b.last_user_message = "нет, не добавляй"
    assert not b.confirm_add_to_cart(prop["proposal_id"])["ok"]
    assert b.view_cart()["items"] == []
    b.last_user_message = "Да, добавь"
    assert b.confirm_add_to_cart(prop["proposal_id"])["ok"]
    assert b.view_cart()["items"][0]["qty"] == 5


def test_qty_not_above_stock():
    b = box()
    stock = b.get_product("SE-EZ9R34225")["in_stock"]
    assert not b.propose_add_to_cart("SE-EZ9R34225", stock + 1)["ok"]


def test_min_order_qty_cable():
    assert not box().propose_add_to_cart("KAB-VVGNG-LS-3X2.5", 5)["ok"]  # кабель от 10 м


# 5) ссылка на корзину
def test_cart_link():
    b = box()
    prop = b.propose_add_to_cart("SE-ATN000143", 2)
    b.cart.turn += 1
    b.last_user_message = "да"
    res = b.confirm_add_to_cart(prop["proposal_id"])
    assert b.cart.id in res["cart_url"]


def test_kazakh_confirmation():
    assert is_explicit_confirmation("Иә, қосыңыз")
    assert not is_explicit_confirmation("жоқ, керек емес")
