import os
import threading
import pandas as pd
import requests
from io import StringIO, BytesIO
from flask import Flask, request
from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext

app = Flask(__name__)

# === ENV ===
TOKEN            = os.getenv("TOKEN")
SELF_URL         = os.getenv("SELF_URL")
ZONES_CSV_URL    = os.getenv("ZONES_CSV_URL")
BRANCH_SHEETS_MAP = {
    k.strip(): v.strip()
    for k, v in (p.split("=", 1) for p in os.getenv("BRANCH_SHEETS_MAP", "").split(",") if "=" in p)
}

bot        = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)
user_states = {}   # user_id -> state

# Загрузка зон доступа
def load_zones():
    """Загружает таблицу зон: сначала пробует CSV, если не сработает — Excel."""
    r = requests.get(ZONES_CSV_URL, timeout=10)
    r.raise_for_status()
    data = r.content
    # сначала CSV
    try:
        df = pd.read_csv(StringIO(data.decode("utf-8-sig")), dtype=str)
    except Exception:
        # упал — пробуем Excel
        df = pd.read_excel(BytesIO(data), dtype=str)
    required = {"Филиал", "РЭС", "ID", "ФИО"}
    if not required.issubset(df.columns):
        raise ValueError(f"В файле зон не все колонки {required!r} есть: {list(df.columns)}")
    zones = {}
    for _, row in df.iterrows():
        uid = str(row["ID"]).strip()
        zones[uid] = {
            "filial": row["Филиал"].strip(),
            "res":    row["РЭС"].strip(),
            "name":   row["ФИО"].strip()
        }
    return zones

# Клавиатуры
def keyboard_rows(labels):
    return ReplyKeyboardMarkup([[l] for l in labels], resize_keyboard=True)

def start_menu(zone):
    if zone["filial"] == "All":
        keys = list(BRANCH_SHEETS_MAP.keys()) + ["Назад"]
        return "Выберите филиал:", keyboard_rows(keys)
    else:
        return "Главное меню:", keyboard_rows(["Поиск ТП", "СПРАВКА"])

HELP_MENU = keyboard_rows([
    "Сечение кабеля",
    "Справка формулы",
    "Назад"
])

# Handler /start
def start(update: Update, context: CallbackContext):
    uid = str(update.effective_user.id)
    zones = load_zones()
    if uid not in zones:
        return update.message.reply_text("У вас нет прав доступа.")
    user_states[uid] = {}
    text, kb = start_menu(zones[uid])
    update.message.reply_text(text, reply_markup=kb)

# Основной хендлер
def handle_message(update: Update, context: CallbackContext):
    uid = str(update.effective_user.id)
    txt = update.message.text.strip()
    zones = load_zones()
    if uid not in zones:
        return update.message.reply_text("У вас нет прав доступа.")
    zone = zones[uid]
    st = user_states.get(uid, {})

    # Назад
    if txt == "Назад":
        user_states[uid] = {}
        text, kb = start_menu(zone)
        return update.message.reply_text(text, reply_markup=kb)

    # All-пользователь выбирает филиал
    if zone["filial"] == "All" and not st.get("step"):
        if txt in BRANCH_SHEETS_MAP:
            user_states[uid] = {"step": "branch_selected", "branch": txt}
            kb = keyboard_rows(["Назад"])
            return update.message.reply_text(f"Введите номер ТП для филиала «{txt}»: ", reply_markup=kb)
        else:
            keys = list(BRANCH_SHEETS_MAP.keys()) + ["Назад"]
            return update.message.reply_text("Пожалуйста, выберите филиал из списка.", reply_markup=keyboard_rows(keys))

    # Конкретный филиал или уже выбрали филиал: поиск ТП или справка
    if zone["filial"] != "All" and not st.get("step"):
        if txt == "Поиск ТП":
            user_states[uid] = {"step": "tp_prompt", "branch": zone["filial"]}
            return update.message.reply_text(f"Введите номер ТП для филиала «{zone['filial']}": ", reply_markup=keyboard_rows(["Назад"]))
        if txt == "СПРАВКА":
            return update.message.reply_text("Справочная информация...", reply_markup=HELP_MENU)

    # Обработка ввода ТП
    if st.get("step") in ("branch_selected", "tp_prompt"):
        branch = st["branch"]
        tp_id = txt
        url = BRANCH_SHEETS_MAP.get(branch)
        try:
            df = pd.read_excel(url, dtype=str)
        except Exception as e:
            return update.message.reply_text(f"Ошибка загрузки данных: {e}")
        if zone["res"] != "All":
            df = df[df["РЭС"].str.strip() == zone["res"]]
        row = df[df["Наименование ТП"].str.strip().eq(tp_id)]
        if row.empty:
            return update.message.reply_text("ТП не найдено. Попробуйте ещё.", reply_markup=keyboard_rows(["Назад"]))
        r = row.iloc[0]
        out = (
            f"ТП: {r['Наименование ТП']}\n"
            f"Ур. напр.: {r['Уровень напряжения']}\n"
            f"ВЛ: {r['Наименование ВЛ']}\n"
            f"ВУ: {r.get('ВУ (если необходимо)','')}\n"
            f"Опоры: {r['Опоры']}\n"
            f"Кол-во опор: {r['Количество опор']}\n"
            f"Провайдер: {r['Наименование Провайдера']}"
        )
        user_states[uid] = {}
        text, kb = start_menu(zone)
        update.message.reply_text(out)
        return update.message.reply_text(text, reply_markup=kb)

    # Fallback
    text, kb = start_menu(zone)
    update.message.reply_text(text, reply_markup=kb)

# Webhook
@app.route("/webhook", methods=["POST"])
def webhook():
    dispatcher.process_update(Update.de_json(request.get_json(force=True), bot))
    return "ok"

@app.route("/")
def index():
    return "OK"

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

if __name__ == "__main__":
    def ping():
        try: requests.get(SELF_URL, timeout=5)
        except: pass
        t = threading.Timer(9*60, ping); t.daemon = True; t.start()
    ping()
    bot.set_webhook(f"{SELF_URL}/webhook")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
