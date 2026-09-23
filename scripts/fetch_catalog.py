"""Скачать страницы ekt.kz и сохранить полные карточки для офлайн-демо.

Запуск: python scripts/fetch_catalog.py --pages 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from agent.catalog import Catalog, DATA_DIR, load_from_api


def fetch_catalog(pages: int, destination: Path = DATA_DIR / "catalog_cache.json") -> int:
    if pages < 1:
        raise ValueError("Число страниц должно быть положительным")

    products = load_from_api(max_pages=pages)
    if not products:
        raise ValueError("API вернул пустой каталог; кэш не изменён")

    catalog = Catalog(products)
    for product in products:
        catalog._ensure_detail(product)

    temporary = destination.with_name(destination.name + ".tmp")
    try:
        temporary.write_text(json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return len(products)


def main() -> None:
    parser = argparse.ArgumentParser(description="Сохранить каталог ekt.kz для офлайн-демо")
    parser.add_argument("--pages", type=int, default=5, help="Число страниц API (по умолчанию: 5)")
    args = parser.parse_args()
    if args.pages < 1:
        parser.error("--pages должно быть положительным числом")

    load_dotenv(ROOT / ".env")
    if not os.getenv("EKT_API_USER") or not os.getenv("EKT_API_PASSWORD"):
        parser.error("Укажите EKT_API_USER и EKT_API_PASSWORD в .env")
    count = fetch_catalog(args.pages)
    print(f"Сохранено {count} товаров в {DATA_DIR / 'catalog_cache.json'}")


if __name__ == "__main__":
    main()
