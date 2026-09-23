"""Отдельный HTTP-интерфейс демо: uvicorn server:app --port 8000."""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from agent.agent import ShopAgent
from agent.cart import Cart
from agent.catalog import Catalog

ROOT = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


class WebCart(Cart):
    """Меняет только адрес ссылки; проверки и изменения корзины наследуются."""
    def __init__(self, catalog: Catalog, base_url: str):
        super().__init__(catalog, cart_id=uuid.uuid4().hex)
        self.base_url = base_url

    @property
    def url(self) -> str:
        return f"{self.base_url}/cart/{self.id}"


@dataclass
class Visitor:
    agent: ShopAgent
    cart: WebCart
    lock: threading.RLock = field(default_factory=threading.RLock)
    history: list = field(default_factory=list)


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    proposal_id: str | None = Field(default=None, max_length=64)

    @field_validator("message")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Сообщение не должно быть пустым")
        return value.strip()


def create_app(catalog: Catalog | None = None, agent_factory=ShopAgent) -> FastAPI:
    app = FastAPI(title="ekt.kz — чат")
    sessions: dict[str, Visitor] = {}
    carts: dict[str, Visitor] = {}
    registry_lock = threading.RLock()
    shared_catalog = catalog

    def lookup(session_id: str) -> Visitor:
        with registry_lock:
            visitor = sessions.get(session_id)
        if visitor is None:
            raise HTTPException(404, "Сессия не найдена. Начните новый чат.")
        return visitor

    def snapshot(session_id: str, visitor: Visitor, answer: str = "") -> dict:
        return {
            "session_id": session_id, "answer": answer,
            "history": list(visitor.history),
            "pending": [{"proposal_id": pid, **{k: v for k, v in prop.items() if k != "turn"}}
                        for pid, prop in visitor.cart.pending.items()],
            "cart": visitor.cart.summary(), "cart_url": visitor.cart.url,
        }

    @app.post("/chat")
    def chat(body: ChatInput, request: Request):
        nonlocal shared_catalog
        session_id = body.session_id
        if session_id is None:
            if body.proposal_id is not None:
                raise HTTPException(400, "Сначала получите предложение в чате.")
            with registry_lock:
                if shared_catalog is None:
                    shared_catalog = Catalog()
                cart = WebCart(shared_catalog, str(request.base_url).rstrip("/"))
                try:
                    agent = agent_factory(shared_catalog, cart)
                except Exception as exc:
                    logger.error("Agent initialization failed: %s", type(exc).__name__)
                    raise HTTPException(503, "Не удалось запустить консультанта.") from None
                visitor = Visitor(agent, cart)
                session_id = uuid.uuid4().hex
                sessions[session_id] = visitor
                carts[cart.id] = visitor
        else:
            visitor = lookup(session_id)
        with visitor.lock:  # два запроса одной сессии не смешивают ходы/подтверждения
            if body.proposal_id is not None:
                if body.proposal_id not in visitor.cart.pending:
                    raise HTTPException(409, "Предложение уже обработано или не принадлежит этой сессии.")
                # Кнопка — отдельное сообщение посетителя. Все проверки остаются в Cart.confirm.
                visitor.cart.turn += 1
                result = visitor.cart.confirm(body.proposal_id, body.message)
                answer = (f"Товар добавлен в корзину: {visitor.cart.url}" if result["ok"] else result["error"])
                visitor.agent.messages.extend([
                    {"role": "user", "content": body.message},
                    {"role": "assistant", "content": answer},
                ])
                visitor.agent.tools.record_message("user", body.message)
                visitor.agent.tools.record_message("assistant", answer)
            else:
                try:
                    answer = visitor.agent.ask(body.message)
                except Exception as exc:
                    logger.error("Chat failed: %s", type(exc).__name__)
                    # Возвращаем session_id и текущее состояние: пользователь не теряет корзину.
                    answer = "Не удалось получить ответ. Попробуйте ещё раз или обратитесь к менеджеру."
            visitor.history.extend([{"role": "user", "content": body.message},
                                    {"role": "assistant", "content": answer}])
            return snapshot(session_id, visitor, answer)

    @app.get("/session/{session_id}")
    def restore(session_id: str):
        visitor = lookup(session_id)
        with visitor.lock:
            return JSONResponse(snapshot(session_id, visitor), headers={"Cache-Control": "no-store"})

    @app.get("/cart/{cart_id}", response_class=HTMLResponse)
    def cart_page(cart_id: str):
        with registry_lock:
            visitor = carts.get(cart_id)
        if visitor is None:
            raise HTTPException(404, "Корзина не найдена")
        with visitor.lock:
            summary = visitor.cart.summary()
        rows = "".join(
            f"<tr><td>{escape(str(i['name']))}<br><small>{escape(str(i['sku']))}</small></td>"
            f"<td>{i['qty']} {escape(str(i['unit']))}</td><td>{i['sum_kzt']:g} ₸</td></tr>"
            for i in summary["items"])
        html = f'''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Корзина ekt.kz</title>
<style>body{{font:16px system-ui;margin:24px auto;padding:0 16px;max-width:800px;color:#172a38}}table{{width:100%;border-collapse:collapse;table-layout:fixed}}td,th{{text-align:left;padding:12px 4px;border-bottom:1px solid #ddd;overflow-wrap:anywhere}}td:first-child,th:first-child{{width:55%}}</style>
<h1>Корзина</h1>{'<table><tr><th>Товар</th><th>Количество</th><th>Сумма</th></tr>' + rows + '</table>' if rows else '<p>Корзина пуста.</p>'}
<p><strong>Итого: {summary['total_kzt']:g} ₸</strong></p><p>Обновите страницу, чтобы увидеть изменения корзины.</p></html>'''
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/", response_class=HTMLResponse)
    def demo():
        return HTMLResponse((ROOT / "static/demo.html").read_text(encoding="utf-8"))

    @app.get("/widget.html", response_class=HTMLResponse)
    @app.get("/static/widget.html", response_class=HTMLResponse)
    def widget():
        return HTMLResponse((ROOT / "static/widget.html").read_text(encoding="utf-8"))

    @app.get("/widget.js")
    def loader():
        return Response((ROOT / "static/widget.js").read_text(encoding="utf-8"), media_type="text/javascript")

    return app


app = create_app()
