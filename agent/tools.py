"""Инструменты агента + их описания для OpenAI function calling."""
from __future__ import annotations

import json

from .cart import Cart
from .catalog import Catalog, load_terms, total_stock


def _card(p: dict, full: bool = False) -> dict:
    c = {"sku": p["sku"], "name": p["name"], "brand": p["brand"], "price_kzt": p["price_kzt"],
         "unit": p["unit"], "in_stock": total_stock(p), "stock_by_warehouse": p["stock"]}
    if full:
        c |= {"category": p["category"], "specs": p["specs"], "certificates": p["certificates"],
              "min_order_qty": p.get("min_order_qty", 1)}
    return c


class Toolbox:
    def __init__(self, catalog: Catalog, cart: Cart):
        self.catalog, self.cart = catalog, cart
        self.last_user_message = ""  # выставляет агент перед каждым ходом

    # --- каталог ---
    def search_products(self, query: str) -> dict:
        found = self.catalog.search(query)
        return {"results": [_card(p) for p in found]} if found else {"results": [], "note": "Ничего не найдено"}

    def get_product(self, sku: str) -> dict:
        p = self.catalog.get(sku)
        return _card(p, full=True) if p else {"error": f"Артикул {sku} не найден"}

    def find_analogs(self, sku: str) -> dict:
        res = self.catalog.analogs(sku)
        return {"analogs": [{**_card(a["product"]), "match_score": a["match_score"],
                             "matching_specs": a["matching_specs"], "differences": a["differences"],
                             "price_diff_kzt": a["price_diff_kzt"]} for a in res]} if res else {"analogs": []}

    def get_purchase_terms(self, topic: str = "all") -> dict:
        t = load_terms()
        return t if topic == "all" or topic not in t else {topic: t[topic], "_note": t["_note"]}

    # --- корзина ---
    def propose_add_to_cart(self, sku: str, qty: int) -> dict:
        return self.cart.propose_add(sku, qty)

    def confirm_add_to_cart(self, proposal_id: str) -> dict:
        return self.cart.confirm(proposal_id, self.last_user_message)

    def view_cart(self) -> dict:
        return {**self.cart.summary(), "cart_url": self.cart.url}

    def call(self, name: str, args_json: str) -> str:
        try:
            result = getattr(self, name)(**json.loads(args_json or "{}"))
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
        return json.dumps(result, ensure_ascii=False)


def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": required or []}}}


TOOL_SCHEMAS = [
    _fn("search_products", "Поиск товаров по названию, артикулу, бренду или характеристикам.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("get_product", "Полная карточка товара: характеристики, сертификаты, цена, остатки по складам.",
        {"sku": {"type": "string"}}, ["sku"]),
    _fn("find_analogs", "Аналоги товара из той же категории, которые есть в наличии, с обоснованием.",
        {"sku": {"type": "string"}}, ["sku"]),
    _fn("get_purchase_terms", "Условия покупки: payment, delivery, min_order, returns, wholesale, manager_contact или all.",
        {"topic": {"type": "string", "enum": ["all", "payment", "delivery", "min_order", "returns", "wholesale", "manager_contact"]}}),
    _fn("propose_add_to_cart", "Подготовить добавление товара в корзину. НЕ меняет корзину — после вызова спроси клиента подтверждение.",
        {"sku": {"type": "string"}, "qty": {"type": "integer"}}, ["sku", "qty"]),
    _fn("confirm_add_to_cart", "Добавить в корзину ранее предложенный товар. Вызывать ТОЛЬКО если клиент в последнем сообщении явно подтвердил.",
        {"proposal_id": {"type": "string"}}, ["proposal_id"]),
    _fn("view_cart", "Показать текущую корзину и ссылку на неё."),
]
