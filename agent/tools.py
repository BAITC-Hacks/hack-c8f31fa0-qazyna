"""Инструменты агента + их описания для OpenAI function calling."""
from __future__ import annotations

import json
import logging
import uuid

logger = logging.getLogger(__name__)

from .cart import Cart
from .catalog import Catalog, load_terms, total_stock
from .handoff import dialogue_summary, safe_excerpt
from .language import translate


def _card(p: dict, full: bool = False) -> dict:
    c = {"sku": p["sku"], "name": p["name"], "brand": p["brand"], "price_kzt": p["price_kzt"],
         "unit": p["unit"], "in_stock": total_stock(p), "stock_by_warehouse": p["stock"]}
    c["specs"] = p["specs"]
    if p.get("data_warning"):
        c["data_warning"] = p["data_warning"]
    if full:
        c |= {"category": p["category"], "specs": p["specs"], "certificates": p["certificates"],
              "min_order_qty": p.get("min_order_qty", 1)}
    return c


class Toolbox:
    def __init__(self, catalog: Catalog, cart: Cart):
        self.catalog, self.cart = catalog, cart
        self.last_user_message = ""  # выставляет агент перед каждым ходом
        self.language = "ru"
        self.dialogue: list[dict] = []
        self.considered_products: dict[str, dict] = {}
        self.manager_request: dict | None = None

    def record_message(self, role: str, text: str, attachment_context: str | None = None):
        message = {"role": role, "content": safe_excerpt(text, 4000)}
        if attachment_context:
            message["attachment"] = safe_excerpt(attachment_context, 4000)
        self.dialogue.append(message)

    def _remember_products(self, products: list[dict]):
        for p in products:
            self.considered_products.pop(p["sku"], None)
            self.considered_products[p["sku"]] = {"sku": p["sku"], "name": p["name"]}

    # --- каталог ---
    def search_products(self, query: str) -> dict:
        found = self.catalog.search(query)
        self._remember_products(found)
        return {"results": [_card(p, full=True) for p in found],
                "catalog_size": len(self.catalog.products),
                "note": "Поиск по локальной выборке. Отсутствие результата не означает отсутствия на сайте."
                        if found else "Точного совпадения в локальной выборке нет. Не повторяй тот же поиск; предложи уточнить артикул или обратиться к менеджеру."}

    def get_product(self, sku: str) -> dict:
        p = self.catalog.get(sku)
        if p:
            self._remember_products([p])
        return _card(p, full=True) if p else {"error": f"Артикул {sku} не найден"}

    def find_analogs(self, sku: str) -> dict:
        res = self.catalog.analogs(sku)
        self._remember_products([a["product"] for a in res])
        return {"analogs": [{**_card(a["product"]), "match_score": a["match_score"],
                             "matching_specs": a["matching_specs"], "differences": a["differences"],
                             "price_diff_kzt": a["price_diff_kzt"]} for a in res]} if res else {"analogs": []}

    def recommend_accessories(self, sku: str) -> dict:
        base = self.catalog.get(sku)
        if base is None:
            return {"error": translate(self.language, "Товар не найден в каталоге.", "Тауар каталогтан табылмады.")}
        self._remember_products([base])
        descriptions = {
            "din_rail": ("DIN-рейка", "Для крепления модульного автомата; проверьте тип крепления и размеры.",
                         "Модульдік автоматты бекіту үшін; бекіту түрі мен өлшемдерін тексеріңіз."),
            "box": ("Бокс", "Для размещения автомата; проверьте число модулей, габариты и степень защиты.",
                    "Автоматты орналастыру үшін; модульдер санын, өлшемдерін және қорғаныс дәрежесін тексеріңіз."),
            "conduit": ("Гофра", "Для прокладки кабеля; проверьте внутренний диаметр, материал и условия монтажа.",
                        "Кабельді төсеу үшін; ішкі диаметрін, материалын және монтаж жағдайларын тексеріңіз."),
        }
        recommendations = []
        for group in self.catalog.accessories(sku):
            title, ru, kk = descriptions[group["kind"]]
            self._remember_products(group["products"])
            recommendations.append({"kind": group["kind"], "category": title,
                                    "reason": translate(self.language, ru, kk),
                                    "compatibility": "requires_check",
                                    "products": [_card(p, full=True) for p in group["products"]]})
        return {"for_sku": base["sku"], "recommendations": recommendations,
                "note": translate(self.language,
                    "Совместимость требует проверки. Если список товаров пуст, доступных позиций этой категории в локальной выборке нет. Подбор уточнит менеджер. Корзина не изменена.",
                    "Үйлесімділікті тексеру қажет. Тауарлар тізімі бос болса, жергілікті каталогта осы санаттың қолжетімді тауарлары жоқ. Менеджер таңдауға көмектеседі. Себет өзгерген жоқ.")}

    def request_manager(self, reason: str = "") -> dict:
        """Готовит обращение в сессии; внешнего канала отправки в демо нет."""
        terms = load_terms()
        reason = reason or translate(self.language, "Клиент просит подключить менеджера.", "Клиент менеджердің көмегін сұрады.")
        request_id = self.manager_request["request_id"] if self.manager_request else uuid.uuid4().hex[:10]
        self.manager_request = {
            "request_id": request_id, "status": "prepared", "language": self.language,
            "summary": dialogue_summary(self.dialogue, list(self.considered_products.values()),
                                        self.cart.summary(), list(self.cart.pending.values()), self.language, reason),
            "contact": terms.get("manager_contact_kk", terms["manager_contact"]) if self.language == "kk" else terms["manager_contact"],
            "note": translate(self.language,
                "Сводка готова. Скачайте её и передайте менеджеру через контакты сайта. Автоматическая отправка не подключена.",
                "Диалог қорытындысы дайын. Оны жүктеп алып, сайттағы байланыс арналары арқылы менеджерге жіберіңіз. Автоматты жіберу қосылмаған."),
        }
        return self.manager_request

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
            if name not in {schema["function"]["name"] for schema in TOOL_SCHEMAS}:
                raise ValueError("Unknown tool")
            result = getattr(self, name)(**json.loads(args_json or "{}"))
        except Exception as e:
            logger.error("Tool %s failed (%s)", name, type(e).__name__)
            result = {"error": f"Инструмент недоступен ({type(e).__name__}). Не делай вывод об отсутствии товара; предложи повторить позже или обратиться к менеджеру."}
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
    _fn("recommend_accessories", "Сопутствующие товары: к автомату — DIN-рейка и бокс, к кабелю — гофра. Только каталог; совместимость требует проверки; корзина не меняется.",
        {"sku": {"type": "string"}}, ["sku"]),
    _fn("request_manager", "Подготовить сводку диалога для менеджера по просьбе клиента. Обращение сохраняется в сессии; автоматической отправки нет.",
        {"reason": {"type": "string"}}),
    _fn("get_purchase_terms", "Условия покупки: payment, delivery, min_order, returns, wholesale, manager_contact или all.",
        {"topic": {"type": "string", "enum": ["all", "payment", "delivery", "min_order", "returns", "wholesale", "manager_contact"]}}),
    _fn("propose_add_to_cart", "Подготовить добавление товара в корзину. НЕ меняет корзину — после вызова спроси клиента подтверждение.",
        {"sku": {"type": "string"}, "qty": {"type": "integer"}}, ["sku", "qty"]),
    _fn("confirm_add_to_cart", "Добавить в корзину ранее предложенный товар. Вызывать ТОЛЬКО если клиент в последнем сообщении явно подтвердил.",
        {"proposal_id": {"type": "string"}}, ["proposal_id"]),
    _fn("view_cart", "Показать текущую корзину и ссылку на неё."),
]
