"""Сводка для передачи менеджеру: только факты диалога и состояние корзины."""
import re

from .language import translate

PAYMENT_DETAILS_RE = re.compile(
    r"\b(?:cvv|cvc|пин|pin|iban|номер\w*\s+(?:\w+\s+){0,2}карт\w*|"
    r"карт\w*\s+нөмір\w*|плат[её]жн\w*\s+(?:данн\w*|реквизит\w*)|"
    r"банковск\w*\s+реквизит\w*|төлем\w*\s+(?:дерек\w*|мәлімет\w*))", re.I)


def safe_excerpt(text: str, limit: int = 600) -> str:
    # Платёжные реквизиты не включаем в обращение и журнал для менеджера.
    text = " ".join(text.split())
    if PAYMENT_DETAILS_RE.search(text):
        # Убираем всю фразу, чтобы не повторить просьбу прислать реквизиты.
        return "[••••]"
    text = re.sub(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)", "[••••]", text)
    return text if len(text) <= limit else text[:limit] + "…"


def dialogue_summary(dialogue: list[dict], products: list[dict], cart: dict,
                     pending: list[dict], language: str, reason: str) -> str:
    def t(ru, kk):
        return translate(language, ru, kk)

    lines = [t("Сводка для менеджера", "Менеджерге арналған диалог қорытындысы"),
             t("Язык клиента: русский", "Клиент тілі: қазақша"),
             t("Причина обращения: ", "Өтініш себебі: ") + safe_excerpt(reason)]
    customers = [m for m in dialogue if m["role"] == "user"]
    if customers:
        lines.append(t("Первый запрос: ", "Алғашқы сұрақ: ") + safe_excerpt(customers[0]["content"]))
        lines.append(t("Последние сообщения клиента:", "Клиенттің соңғы хабарламалары:"))
        lines.extend("- " + safe_excerpt(m["content"]) for m in customers[-4:])
    else:
        lines.append(t("Клиент ещё не описал вопрос.", "Клиент сұрағын әлі сипаттаған жоқ."))
    replies = [m for m in dialogue if m["role"] == "assistant"]
    if replies:
        lines.append(t("Последний ответ консультанта: ", "Кеңесшінің соңғы жауабы: ")
                     + safe_excerpt(replies[-1]["content"]))
    attachments = [m["attachment"] for m in customers if m.get("attachment")]
    if attachments:
        lines.append(t("Проверка вложений:", "Тіркемелерді тексеру:"))
        lines.extend("- " + safe_excerpt(context, 1200) for context in attachments[-2:])
    if products:
        lines.append(t("Товары, рассмотренные в диалоге:", "Диалогта қаралған тауарлар:"))
        lines.extend(f"- {p['sku']}: {p['name']}" for p in products[-6:])
    lines.append(t("Подтверждённая корзина:", "Расталған себет:"))
    lines.extend(f"- {p['sku']}: {p['name']} × {p['qty']} {p['unit']}" for p in cart["items"])
    if not cart["items"]:
        lines.append(t("Пуста.", "Бос."))
    if pending:
        lines.append(t("Предложения, НЕ подтверждённые клиентом:", "Клиент растамаған ұсыныстар:"))
        lines.extend(f"- {p['sku']}: {p['name']} × {p['qty']}" for p in pending)
    lines.append(t("Следующий шаг: менеджеру уточнить запрос и совместимость выбранных товаров.",
                   "Келесі қадам: менеджер клиенттің сұрағын және таңдалған тауарлардың үйлесімділігін нақтылауы керек."))
    return "\n".join(lines)
