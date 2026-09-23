"""Демо чат-виджета ekt.kz. Запуск: streamlit run app.py"""
import streamlit as st

from agent.agent import ShopAgent
from agent.cart import CartStore
from agent.catalog import Catalog

st.set_page_config(page_title="ekt.kz — ИИ-консультант", page_icon="⚡", layout="centered")


@st.cache_resource
def get_store() -> CartStore:  # общий для всех вкладок, чтобы ссылка на корзину работала
    return CartStore(Catalog())


store = get_store()

# ---------- страница корзины: /?cart=<id> ----------
cart_param = st.query_params.get("cart")
if cart_param and "agent" not in st.session_state:
    cart = store.carts.get(cart_param)
    st.title("🛒 Корзина")
    if not cart or not cart.items:
        st.info("Корзина пуста или не найдена.")
    else:
        s = cart.summary()
        st.table([{"Артикул": i["sku"], "Товар": i["name"], "Кол-во": f"{i['qty']} {i['unit']}",
                   "Сумма, ₸": f"{i['sum_kzt']:,}".replace(",", " ")} for i in s["items"]])
        st.subheader(f"Итого: {s['total_kzt']:,} ₸".replace(",", " "))
        st.button("Перейти к оформлению заказа", type="primary", disabled=True,
                  help="В проде — переход в checkout ekt.kz")
    st.stop()

# ---------- чат ----------
if "agent" not in st.session_state:
    cart = store.get_or_create()
    st.session_state.agent = ShopAgent(store.catalog, cart)
    st.session_state.chat = [("assistant", "Здравствуйте! Я консультант ekt.kz. Спросите про товар, наличие, аналоги или условия покупки.")]
agent: ShopAgent = st.session_state.agent
cart = agent.tools.cart

st.title("⚡ ИИ-консультант ekt.kz")


def send(text: str):
    st.session_state.chat.append(("user", text))
    with st.spinner("Ищу в каталоге..."):
        st.session_state.chat.append(("assistant", agent.ask(text)))


for role, text in st.session_state.chat:
    st.chat_message(role).write(text)

# Кнопки подтверждения для ожидающих предложений (явное подтверждение кликом)
for pid, prop in list(cart.pending.items()):
    c1, c2 = st.columns([3, 1])
    c1.info(f"Добавить **{prop['name']}** × {prop['qty']}?")
    if c2.button("✅ Да, добавить", key=f"ok-{pid}"):
        send(f"Да, добавь (предложение {pid})")
        st.rerun()

examples = ["Есть ли в наличии автомат IEK 25А 1P?", "Покажите сертификат на УЗО EKF 25А 30мА",
            "Какие условия доставки в Алматы?"]
cols = st.columns(len(examples))
for col, ex in zip(cols, examples):
    if col.button(ex, use_container_width=True):
        send(ex)
        st.rerun()

if q := st.chat_input("Напишите вопрос…"):
    send(q)
    st.rerun()

# ---------- боковая панель ----------
with st.sidebar:
    s = cart.summary()
    st.header(f"🛒 Корзина ({len(s['items'])})")
    for i in s["items"]:
        st.write(f"{i['name']} — {i['qty']} {i['unit']}")
    if s["items"]:
        st.write(f"**Итого: {s['total_kzt']:,} ₸**".replace(",", " "))
        st.link_button("Открыть корзину", cart.url)
    with st.expander("🔧 Вызовы инструментов (для жюри)"):
        for t in agent.trace[-8:]:
            st.code(f"{t['tool']}({t['args']})\n→ {t['result'][:300]}")
