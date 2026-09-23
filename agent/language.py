"""Язык диалога и короткие тексты интерфейса без обращения к модели."""
import re


def detect_language(text: str, previous: str = "ru") -> str:
    text = text.lower()
    if re.search(r"по-русски|на русском|орысша|орыс тілінде", text):
        return "ru"
    if re.search(r"на казахском|қазақша|қазақ тілінде|[әғқңөұүһі]", text):
        return "kk"
    if re.search(r"\b(салем|керек|бар|рахмет|себет|болады)\b", text):
        return "kk"
    if re.search(r"\b(здравствуйте|нужен|нужна|нужно|нужны|есть|покажите|сколько|доставка|спасибо|какие|какой|какая|как|когда|где|можно)\b", text):
        return "ru"
    # Артикул, число или короткое подтверждение не переключают язык.
    return previous


def translate(language: str, ru: str, kk: str) -> str:
    return kk if language == "kk" else ru
