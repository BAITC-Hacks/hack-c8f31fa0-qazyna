"""Полный локальный кэш каталога создаётся и читается без сети."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent import catalog
from scripts import fetch_catalog


DATA = Path(__file__).resolve().parents[1] / "data"


def test_fetch_and_load_cache_offline(tmp_path, monkeypatch):
    detail = json.loads((DATA / "api_sample_detail.json").read_text(encoding="utf-8"))
    detail["name"] += " — Қазақша: Ә Ғ Қ Ң Ө Ұ Ү Һ І"
    listing = {key: detail[key] for key in ("id", "article", "name", "price")}
    pages = Mock(return_value=[catalog.normalize_api_product(listing)])
    get_detail = Mock(return_value=detail)
    monkeypatch.setattr(fetch_catalog, "load_from_api", pages)
    monkeypatch.setattr(catalog, "_api_get", get_detail)

    destination = tmp_path / "catalog_cache.json"
    assert fetch_catalog.fetch_catalog(3, destination) == 1
    pages.assert_called_once_with(max_pages=3)
    get_detail.assert_called_once_with("products/detail", {"id": detail["id"]})

    saved = json.loads(destination.read_text(encoding="utf-8"))
    assert detail["name"].encode(encoding="utf-8") in destination.read_bytes()
    assert saved[0]["name"] == detail["name"]
    assert saved[0]["sku"] == detail["article"]
    assert saved[0]["stock"]["Алматы"] == 5
    assert saved[0]["specs"]["Номинальный ток"] == "250 А"
    assert not saved[0]["_detail_pending"]

    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path)
    monkeypatch.setenv("USE_LIVE_API", "1")
    monkeypatch.setenv("EKT_API_USER", "test-user")
    monkeypatch.setattr(catalog, "load_from_api", Mock(side_effect=AssertionError("network used")))
    offline = catalog.Catalog()
    assert offline.get(detail["article"])["stock"]["Алматы"] == 5
    assert offline.get(detail["article"])["name"] == detail["name"]


def test_failed_detail_keeps_existing_cache(tmp_path, monkeypatch):
    detail = json.loads((DATA / "api_sample_detail.json").read_text(encoding="utf-8"))
    listing = {key: detail[key] for key in ("id", "article", "name", "price")}
    monkeypatch.setattr(fetch_catalog, "load_from_api", lambda max_pages: [catalog.normalize_api_product(listing)])
    monkeypatch.setattr(catalog, "_api_get", Mock(side_effect=RuntimeError("API unavailable")))
    destination = tmp_path / "catalog_cache.json"
    destination.write_text("previous cache", encoding="utf-8")

    with pytest.raises(RuntimeError):
        fetch_catalog.fetch_catalog(1, destination)
    assert destination.read_text(encoding="utf-8") == "previous cache"
