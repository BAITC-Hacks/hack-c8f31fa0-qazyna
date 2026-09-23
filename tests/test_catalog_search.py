"""Поиск по клиентским запросам, без сети и локального кэша каталога."""
import json
from pathlib import Path

import pytest

from agent.catalog import Catalog, total_stock


@pytest.fixture
def catalog():
    source = Path(__file__).resolve().parents[1] / "data" / "catalog_sample.json"
    return Catalog(json.loads(source.read_text(encoding="utf-8")))


@pytest.mark.parametrize(("query", "expected_sku"), [
    ("Нужен автомат на 16 ампер", "IEK-MVA20-1-016-C"),
    ("Есть автмоат?", "IEK-MVA20-1-016-C"),
    ("Устройство защитного отключения 25 ампер 30мА", "EKF-ELCB-2-25-30"),
    ("УЗО 25 А Schneider", "SE-EZ9R34225"),
    ("Нужна розтека", "SE-ATN000143"),
])
def test_typical_customer_queries(catalog, query, expected_sku):
    found = catalog.search(query)
    assert found
    assert found[0]["sku"] == expected_sku


@pytest.mark.parametrize(("short", "full"), [
    ("автомат", "автоматический выключатель"),
    ("узо", "устройство защитного отключения"),
    ("А", "ампер"),
])
def test_synonyms_work_in_both_directions(catalog, short, full):
    # Убираем прочие поля, чтобы результат зависел только от синонима.
    product = {**catalog.products[0], "sku": "test", "name": full,
               "category": "", "brand": "", "specs": {}}
    isolated = Catalog([product])
    assert isolated.search(short) == [product]
    product["name"] = short
    assert isolated.search(full) == [product]


def test_ampere_spellings_return_the_same_ranking(catalog):
    expected = catalog.search("автомат 25 ампер")
    for current in ("25А", "25 А", "25A", "25 A", "25 ампера", "25 амперов"):
        assert catalog.search(f"автомат {current}") == expected


def test_typo_inside_synonym_phrase(catalog):
    product = {**catalog.products[0], "sku": "test", "name": "автомат",
               "category": "", "brand": "", "specs": {}}
    assert Catalog([product]).search("автоматическй выключатель") == [product]


@pytest.mark.parametrize("query", ["", "?!", "26", "IP21", "EZ9F34126", "узл"])
def test_empty_or_unmatched_numbers_codes_and_abbreviations(catalog, query):
    assert catalog.search(query) == []


def test_exact_sku_and_stock_tiebreaker(catalog):
    sku = "IEK-MVA20-1-025-C"
    assert catalog.search(f" {sku.lower()} ") == [catalog.get(sku)]
    found = catalog.search("автомат 25 ампер", limit=4)
    assert len(found) == 4
    assert all(p["specs"]["Номинальный ток, А"] == 25 for p in found)
    assert all(total_stock(p) > 0 for p in found[:3])
    assert found[-1]["sku"] == sku


def test_single_word_from_synonym_phrase_still_matches(catalog):
    assert catalog.search("выключатель")[0]["category"] == "Автоматические выключатели"
