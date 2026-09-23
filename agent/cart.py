"""Корзина с защитой: изменить её можно ТОЛЬКО через подтверждённое предложение.

Поток:
 1. агент вызывает propose_add(sku, qty) → создаётся «черновик» (proposal), корзина НЕ меняется;
 2. клиент явно подтверждает («да, добавь» или кнопка в UI);
 3. confirm(proposal_id, last_user_message) проверяет подтверждение в КОДЕ
    (а не на слово модели) и остатки, и только тогда меняет корзину.
"""
from __future__ import annotations

import os
import re
import uuid

from .catalog import Catalog, total_stock

CONFIRM_RE = re.compile(r"\b(да|добав\w*|подтвер\w*|согласен|согласна|оформля\w*|беру|ок|окей|yes|иә|иа|қосыңыз|қосшы|қосыңызшы|растаймын|растаңыз)\b", re.I)
NEGATION_RE = re.compile(r"\b(нет|не\s+(надо|нужно|добав\w*|беру)|отмен\w*|жоқ|(?:керек|қажет)\s+емес|қоспа\w*|растама\w*|алмай\w*|бас\s+тарт\w*)\b", re.I)


def is_explicit_confirmation(text: str) -> bool:
    text = (text or "").lower()
    confirmed = CONFIRM_RE.search(text) or re.fullmatch(r"\s*(?:себетке\s+)?қос[.!]?\s*", text)
    return bool(confirmed) and not NEGATION_RE.search(text)


class Cart:
    def __init__(self, catalog: Catalog, cart_id: str | None = None):
        self.catalog = catalog
        self.id = cart_id or uuid.uuid4().hex[:10]
        self.items: dict[str, int] = {}
        self.pending: dict[str, dict] = {}
        self.turn = 0  # номер сообщения клиента; агент увеличивает его на каждом ходе

    # ---- шаг 1: предложение ----
    def propose_add(self, sku: str, qty: int) -> dict:
        p = self.catalog.get(sku)
        if not p:
            return {"ok": False, "error": f"Товар {sku} не найден в каталоге"}
        qty = int(qty)
        available = total_stock(p) - self.items.get(p["sku"], 0)
        if qty < p.get("min_order_qty", 1):
            return {"ok": False, "error": f"Минимальная партия: {p['min_order_qty']} {p['unit']}"}
        if available <= 0:
            return {"ok": False, "error": "Товара нет в наличии", "available": 0}
        if qty > available:
            return {"ok": False, "error": f"Можно добавить не больше {available} {p['unit']} (остаток)", "available": available}
        pid = uuid.uuid4().hex[:6]
        self.pending[pid] = {"sku": p["sku"], "qty": qty, "name": p["name"], "price_kzt": p["price_kzt"], "turn": self.turn}
        return {"ok": True, "proposal_id": pid, **{k: v for k, v in self.pending[pid].items() if k != "turn"},
                "sum_kzt": qty * (p["price_kzt"] or 0),
                "note": "Корзина НЕ изменена. Спроси клиента явное подтверждение."}

    # ---- шаг 2: подтверждение ----
    def confirm(self, proposal_id: str, last_user_message: str) -> dict:
        prop = self.pending.get(proposal_id)
        if not prop:
            return {"ok": False, "error": "Предложение не найдено или уже обработано"}
        if self.turn <= prop["turn"]:  # подтверждение должно прийти ОТДЕЛЬНЫМ сообщением после предложения
            return {"ok": False, "error": "Сначала покажи клиенту предложение и дождись его ответа."}
        if not is_explicit_confirmation(last_user_message):
            return {"ok": False, "error": "Нет явного подтверждения от клиента. Корзина не изменена."}
        p = self.catalog.get(prop["sku"])
        available = total_stock(p) - self.items.get(p["sku"], 0)
        if prop["qty"] > available:  # остаток мог измениться
            return {"ok": False, "error": f"Остаток изменился: доступно {available}"}
        self.items[p["sku"]] = self.items.get(p["sku"], 0) + prop["qty"]
        del self.pending[proposal_id]
        prop = {k: v for k, v in prop.items() if k != "turn"}
        return {"ok": True, "added": prop, "cart": self.summary(), "cart_url": self.url}

    def remove(self, sku: str) -> None:
        self.items.pop(sku, None)

    @property
    def url(self) -> str:
        base = os.getenv("CART_BASE_URL", "http://localhost:8501/")
        return f"{base}?cart={self.id}"

    def summary(self) -> dict:
        lines = []
        for sku, qty in self.items.items():
            p = self.catalog.get(sku)
            lines.append({"sku": sku, "name": p["name"], "qty": qty, "unit": p["unit"],
                          "price_kzt": p["price_kzt"], "sum_kzt": qty * (p["price_kzt"] or 0)})
        return {"cart_id": self.id, "items": lines, "total_kzt": sum(l["sum_kzt"] for l in lines)}


class CartStore:
    """Хранилище корзин в памяти (для прототипа). В проде — БД/сессии сайта."""
    def __init__(self, catalog: Catalog):
        self.catalog, self.carts = catalog, {}

    def get_or_create(self, cart_id: str | None = None) -> Cart:
        if cart_id and cart_id in self.carts:
            return self.carts[cart_id]
        c = Cart(self.catalog, cart_id)
        self.carts[c.id] = c
        return c
