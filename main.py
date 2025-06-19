import os
import threading
import pandas as pd
import requests
from io import StringIO
from flask import Flask, request
from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext

app = Flask(__name__)

# === ENV ===
TOKEN           = os.getenv("TOKEN")
SELF_URL        = os.getenv("SELF_URL")
ZONES_CSV_URL   = os.getenv("ZONES_CSV_URL")

# Ссылки на справочные картинки (Google Drive)
IMG_CABLE_URL       = "https://drive.google.com/uc?export=download&id=11LaH-BvqtUTk5UVLEn5yGUOeCsWQkaX"
IMG_SELECTIVITY_URL = "https://drive.google.com/uc?export=download&id=11q0orVtOJ_UTk5UVLEn5yGUOeCsWQkaX"
IMG_FORMULAS_URL    = "https://drive.google.com/uc?export=download&id=1StUq8JSdpU1QvJJ6F3W3dHZnReF6kt8"

bot        = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# Состояния и рассылка
user_states = {}   # user_id -> {"mode","region","name"}
known_users = set()

# Предзагрузка зон доступа
def load_zones_map():
    r = requests.get(ZONES_CSV_URL, timeout=10)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.content.decode("utf-8-sig")), dtype=str)
    # Определяем колонку с именем
    name_col = None
    for col in ("Name", "Имя"):
        if col in df.columns:
            name_col = col
            break
    if not name_col and len(df.columns) >= 3:
        name_col = df.columns[2]
    zones = {}
    for _, row in df.iterrows():
        uid    = row["ID"].strip()
        region = row["Region"].strip()
        name   = row[name_col].strip() if name_col and pd.notna(row.get(name_col)) else ""
        zones[uid] = {"region": region, "name": name}
    return zones

# Клавиатуры
def main_menu(region):
    items = ["СПРАВКА", "ВОЛС"]
    if region.lower() == "admin":
        items.append("Уведомление всем")
    return ReplyKeyboardMarkup([items], resize_keyboard=True)

HELP_MENU = ReplyKeyboardMarkup([
    ["Сечение кабеля (ток, мощность)"],
    ["Номиналы ВА (ток, мощность)"],
    ["Формулы"],
    ["Назад"]
], resize_keyboard=True)

# Handlers
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Главное меню:", reply_markup=main_menu(""))

def handle_message(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    text    = update.message.text.strip()

    # Зона доступа
    try:
        zones = load_zones_map()
    except Exception as e:
        return update.message.reply_text(f"Ошибка загрузки зон: {e}")
    info = zones.get(user_id)
    if not info:
        return update.message.reply_text("У вас нет прав или не назначены в РЭС.")
    region, user_name = info["region"], info["name"]
    known_users.add(user_id)
    state = user_states.get(user_id, {})

    # Режим рассылки (только admin)
    if state.get("mode") == "broadcast":
        for uid in known_users:
            try:
                bot.send_message(chat_id=int(uid), text=text)
            except:
                pass
        user_states[user_id] = {}
        return update.message.reply_text("Рассылка выполнена.", reply_markup=main_menu(region))

    # Главное меню
    if text == "СПРАВКА":
        user_states[user_id] = {"mode":"help", "region":region}
        return update.message.reply_text("Справочная информация:", reply_markup=HELP_MENU)

    if text == "ВОЛС":
        user_states[user_id] = {"mode":"vols", "region":region}
        return update.message.reply_text("Введите номер ВОЛС (например: К1):", reply_markup=main_menu(region))

    if text == "Уведомление всем" and region.lower()=="admin":
        user_states[user_id] = {"mode":"broadcast", "region":region}
        return update.message.reply_text("Введите текст для рассылки всем:", reply_markup=main_menu(region))

    # Справка
    if state.get("mode") == "help":
        if text == "Сечение кабеля (ток, мощность)":
            update.message.reply_photo(photo=IMG_CABLE_URL)
        elif text == "Номиналы ВА (ток, мощность)":
            update.message.reply_photo(photo=IMG_SELECTIVITY_URL)
        elif text == "Формулы":
            update.message.reply_photo(photo=IMG_FORMULAS_URL)
        elif text == "Назад":
            user_states[user_id] = {}
            return update.message.reply_text("Главное меню:", reply_markup=main_menu(region))
        else:
            return update.message.reply_text("Выберите раздел справки:", reply_markup=HELP_MENU)
        return update.message.reply_text("Справочная информация:", reply_markup=HELP_MENU)

    # ВОЛС
    if state.get("mode") == "vols":
        if text.upper() == "НАЗАД":
            user_states[user_id] = {}
            return update.message.reply_text("Главное меню:", reply_markup=main_menu(region))

        vols_input = text.strip()
        # TODO: добавить логику поиска ВОЛС и выдачи PDF
        update.message.reply_text(f"Вы ввели ВОЛС: {vols_input}
Здесь будет логика выдачи PDF.", reply_markup=main_menu(region))
        user_states[user_id] = {}
        return

    # fallback
    update.message.reply_text("Главное меню:", reply_markup=main_menu(region))

# Webhook & запуск
@app.route("/webhook", methods=["POST"])
def webhook():
    dispatcher.process_update(Update.de_json(request.get_json(force=True), bot))
    return "ok"

@app.route("/")
def index():
    return "Бот работает"

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

if __name__ == "__main__":
    def awake():
        try: requests.get(SELF_URL, timeout=5)
        except: pass
        t = threading.Timer(9*60, awake)
        t.daemon = True
        t.start()
    awake()
    bot.set_webhook(f"{SELF_URL}/webhook")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
