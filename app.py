"""Демо чат-виджета ekt.kz. Запуск: streamlit run app.py"""
import streamlit as st

from agent.agent import ShopAgent
from agent.attachments import extract_text, specification_context
from agent.cart import CartStore
from agent.catalog import Catalog
from agent.language import translate

st.set_page_config(page_title="ekt.kz — ИИ-консультант", page_icon="⚡", layout="centered")


@st.cache_resource
def get_store() -> CartStore:  # общий для всех вкладок, чтобы ссылка на корзину работала
    return CartStore(Catalog())


store = get_store()
language_choice = st.sidebar.selectbox("Язык / Тіл", ["auto", "ru", "kk"],
                                       format_func=lambda value: {"auto": "Авто / Автоматты", "ru": "Русский", "kk": "Қазақша"}[value])
language = language_choice if language_choice != "auto" else getattr(st.session_state.get("agent"), "language", "ru")


def t(ru: str, kk: str) -> str:
    return translate(language, ru, kk)

# ---------- страница корзины: /?cart=<id> ----------
cart_param = st.query_params.get("cart")
if cart_param and "agent" not in st.session_state:
    cart = store.carts.get(cart_param)
    st.title(t("🛒 Корзина", "🛒 Себет"))
    if not cart or not cart.items:
        st.info(t("Корзина пуста или не найдена.", "Себет бос немесе табылмады."))
    else:
        s = cart.summary()
        st.table([{"Артикул": i["sku"], t("Товар", "Тауар"): i["name"], t("Кол-во", "Саны"): f"{i['qty']} {i['unit']}",
                   t("Сумма, ₸", "Сома, ₸"): f"{i['sum_kzt']:,}".replace(",", " ")} for i in s["items"]])
        st.subheader((t("Итого: ", "Барлығы: ") + f"{s['total_kzt']:,} ₸").replace(",", " "))
        st.button(t("Перейти к оформлению заказа", "Тапсырысты рәсімдеу"), type="primary", disabled=True,
                  help=t("Оформление заказа будет доступно на ekt.kz", "Тапсырысты ekt.kz сайтында рәсімдеуге болады"))
    st.stop()

# ---------- чат ----------
if "agent" not in st.session_state:
    cart = store.get_or_create()
    st.session_state.agent = ShopAgent(store.catalog, cart)
    st.session_state.chat = [("assistant", t(
        "Здравствуйте! Я консультант ekt.kz. Спросите про товар, наличие, аналоги или условия покупки.",
        "Сәлеметсіз бе! Мен ekt.kz кеңесшісімін. Тауарлар, олардың бар-жоғы, баламалары немесе сатып алу шарттары туралы сұраңыз."))]
agent: ShopAgent = st.session_state.agent
if language_choice != "auto":
    agent.set_language(language_choice)
cart = agent.tools.cart

st.title(t("⚡ ИИ-консультант ekt.kz", "⚡ ekt.kz ЖИ кеңесшісі"))


def send(text: str, attachment_context: str | None = None, display_text: str | None = None):
    st.session_state.chat.append(("user", display_text or text))
    with st.spinner(t("Ищу в каталоге...", "Каталогтан іздеп жатырмын...")):
        st.session_state.chat.append(("assistant", agent.ask(text, attachment_context=attachment_context,
                                      language=None if language_choice == "auto" else language_choice)))


for role, text in st.session_state.chat:
    st.chat_message(role).write(text)

if st.button(t("Позвать менеджера", "Менеджерді шақыру"), key="request-manager"):
    agent.tools.request_manager()
    st.rerun()

if request := agent.tools.manager_request:
    with st.expander(t("Обращение к менеджеру", "Менеджерге өтініш"), expanded=True):
        st.info(request["note"])
        st.text(request["summary"])
        st.write(request["contact"])
        st.download_button(t("Скачать сводку диалога", "Диалог қорытындысын жүктеу"), request["summary"].encode(encoding="utf-8"),
                           file_name=f"manager-{request['request_id']}.txt", mime="text/plain; charset=utf-8")
        st.caption(t("Чтобы обновить сводку, снова нажмите «Позвать менеджера».",
                     "Қорытындыны жаңарту үшін «Менеджерді шақыру» түймесін қайта басыңыз."))

# Кнопки подтверждения для ожидающих предложений (явное подтверждение кликом)
for pid, prop in list(cart.pending.items()):
    c1, c2 = st.columns([3, 1])
    c1.info(t(f"Добавить **{prop['name']}** × {prop['qty']}?", f"**{prop['name']}** × {prop['qty']} себетке қосайын ба?"))
    if c2.button(t("✅ Да, добавить", "✅ Иә, қосу"), key=f"ok-{pid}"):
        send(t(f"Да, добавь (предложение {pid})", f"Иә, қосыңыз (ұсыныс {pid})"))
        st.rerun()

examples = [t("Есть ли в наличии автомат IEK 25А 1P?", "IEK 25А 1P автоматы бар ма?"),
            t("Покажите сертификат на 010500008_", "010500008_ сертификатын көрсетіңіз"),
            t("Какие условия доставки в Алматы?", "Алматыға жеткізу шарттары қандай?")]
cols = st.columns(len(examples))
for col, ex in zip(cols, examples):
    if col.button(ex, use_container_width=True):
        send(ex)
        st.rerun()

uploaded = st.file_uploader(t("Приложить спецификацию", "Спецификацияны тіркеу"), type=["xlsx", "docx", "pdf", "jpg", "jpeg", "png"])
if st.button(t("Проверить спецификацию", "Спецификацияны тексеру"), disabled=uploaded is None):
    try:
        extracted = extract_text(uploaded.name, uploaded.getvalue(), vision_client=agent.client)
        context = specification_context(extracted, store.catalog)
    except ValueError as exc:
        st.error(t(str(exc), "Файлды оқу мүмкін болмады. Форматын, өлшемін және тауарлар тізімін тексеріңіз."))
    except Exception:
        st.error(t("Не удалось обработать файл. Проверьте формат и повторите попытку.",
                   "Файлды өңдеу мүмкін болмады. Форматын тексеріп, қайталап көріңіз."))
    else:
        send(t("Проверь приложенную спецификацию.", "Тіркелген спецификацияны тексеріңіз."), attachment_context=context,
             display_text=t(f"Приложена спецификация: {uploaded.name}", f"Спецификация тіркелді: {uploaded.name}"))
        st.rerun()

if q := st.chat_input(t("Напишите вопрос…", "Сұрағыңызды жазыңыз…")):
    send(q)
    st.rerun()

# ---------- боковая панель ----------
with st.sidebar:
    s = cart.summary()
    st.header(t(f"🛒 Корзина ({len(s['items'])})", f"🛒 Себет ({len(s['items'])})"))
    for i in s["items"]:
        st.write(f"{i['name']} — {i['qty']} {i['unit']}")
    if s["items"]:
        st.write((t("**Итого: ", "**Барлығы: ") + f"{s['total_kzt']:,} ₸**").replace(",", " "))
        st.link_button(t("Открыть корзину", "Себетті ашу"), cart.url)
    with st.expander(t("🔧 Вызовы инструментов (для жюри)", "🔧 Құрал шақырулары (қазылар алқасына)")):
        for entry in agent.trace[-8:]:
            st.code(f"{entry['tool']}({entry['args']})\n→ {entry['result'][:300]}")
