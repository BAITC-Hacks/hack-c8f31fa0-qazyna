"""Кнопка менеджера и казахский интерфейс работают без вызова модели."""
import json
from pathlib import Path
from unittest.mock import Mock

import streamlit as st
from streamlit.testing.v1 import AppTest

from agent import agent as module


def test_manager_button_summary_and_language_switch(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    products = json.loads((root / "data" / "catalog_sample.json").read_text(encoding="utf-8"))
    client = Mock()
    download = Mock(wraps=st.download_button)
    monkeypatch.setattr(st, "download_button", download)
    monkeypatch.setattr(module, "OpenAI", lambda: client)
    monkeypatch.setattr("agent.catalog.load_catalog", lambda: products)
    st.cache_resource.clear()
    try:
        app = AppTest.from_file(str(root / "app.py")).run()
        assert not app.exception
        assert app.button(key="request-manager").label == "Позвать менеджера"
        app.button(key="request-manager").click().run()
        assert not app.exception
        request = app.session_state["agent"].tools.manager_request
        assert request["status"] == "prepared"
        assert any("Сводка для менеджера" in text.value for text in app.text)
        assert app.get("download_button")
        assert not app.text_input
        assert download.call_args.args[1].decode(encoding="utf-8") == request["summary"]
        assert download.call_args.kwargs["mime"] == "text/plain; charset=utf-8"
        app.selectbox[0].set_value("kk").run()
        assert app.button(key="request-manager").label == "Менеджерді шақыру"
        app.button(key="request-manager").click().run()
        assert not app.exception
        updated = app.session_state["agent"].tools.manager_request
        assert updated["request_id"] == request["request_id"]
        assert "Клиент тілі: қазақша" in updated["summary"]
        assert download.call_args.args[1].decode(encoding="utf-8") == updated["summary"]
        assert app.session_state["agent"].tools.cart.items == {}
        client.chat.completions.create.assert_not_called()
    finally:
        st.cache_resource.clear()
