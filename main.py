import os
import re
from io import StringIO, BytesIO

import pandas as pd
import requests
from flask import Flask, request
from telegram import Bot, Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext

app = Flask(__name__)

# === ENV ===
TOKEN             = os.getenv("TOKEN")
SELF_URL          = os.getenv("SELF_URL")
ZONES_CSV_URL     = os.getenv("ZONES_CSV_URL")
BRANCH_SHEETS_MAP = {
    k.strip(): v.strip()
    for k, v in (p.split("=", 1) for p in os.getenv("BRANCH_SHEETS_MAP", "").split(",") if "=" in p)
}

bot        = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)
user_states = {}   # user_id -> state

# --- Утилиты ---

def normalize_sheet_url(url, default_gid=0):
    """
    Преобразует общий URL Google Sheets в прямую ссылку экспорта CSV.
    """
    if '/export' in url:
        return url
    m = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    if not m:
        return url
    sheet_id = m.group(1)
    return f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={default_gid}'


def load_zones():
    """Загружает таблицу зон доступа и возвращает словарь по ID пользователя."""
    url = normalize_sheet_url(ZONES_CSV_URL)
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.content
    try:
        df = pd.read_csv(StringIO(data.decode("utf-8-sig")), dtype=str)
    except Exception:
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

# --- Меню клавиатур ---

def main_menu_keyboard():
    return ReplyKeyboardMarkup([["Поиск"], ["Справочная информация"], ["Уведомление всем"]], resize_keyboard=True)

# --- Хендлеры ---

def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        f"Здравствуйте, {load_zones().get(str(update.message.from_user.id),{{}}).get('name','пользователь')}!",
        reply_markup=main_menu_keyboard()
    )


def handle_text(update: Update, context: CallbackContext):
    text = update.message.text
    user_id = str(update.message.from_user.id)
    zones = load_zones()
    state = user_states.get(user_id)
    if text == "Поиск":
        update.message.reply_text("Введите номер счётчика:", reply_markup=ReplyKeyboardRemove())
        user_states[user_id] = "AWAIT_NUMBER"
    elif state == "AWAIT_NUMBER":
        update.message.reply_text(
            f"Запрос принят для номера {text}. Выберите тип информации.",
            reply_markup=ReplyKeyboardMarkup(
                [["Договор"], ["Адрес подключения"], ["Прибор учёта"], ["Назад"]],
                resize_keyboard=True
            )
        )
        user_states[user_id] = ("AWAIT_INFO", text)
    elif isinstance(state, tuple) and state[0] == "AWAIT_INFO":
        _, number = state
        info_type = text
        update.message.reply_text(f"Информация ({info_type}) по {number}: ...")
        update.message.reply_text("Возвращаемся в главное меню.", reply_markup=main_menu_keyboard())
        user_states.pop(user_id, None)
    elif text == "Справочная информация":
        update.message.reply_text(
            "Выберите раздел:",
            reply_markup=ReplyKeyboardMarkup(
                [["Сечение кабеля (ток, мощность)"], ["Номиналы ВА (ток, мощность)"], ["Формулы"], ["Назад"]],
                resize_keyboard=True
            )
        )
        user_states[user_id] = "AWAIT_HELP"
    elif state == "AWAIT_HELP":
        if text == "Назад":
            update.message.reply_text("Главное меню:", reply_markup=main_menu_keyboard())
            user_states.pop(user_id, None)
        else:
            file_map = {
                "Сечение кабеля (ток, мощность)": "sechenie.jpeg",
                "Номиналы ВА (ток, мощность)": "selectivity.jpeg",
                "Формулы": "formuly.jpeg"
            }
            filename = file_map.get(text)
            if filename and os.path.exists(filename):
                update.message.reply_photo(open(filename, 'rb'))
            else:
                update.message.reply_text("Картинка не найдена.")
    elif text == "Уведомление всем":
        if zones.get(user_id, {}).get('res') == 'admin':
            update.message.reply_text("Введите сообщение для рассылки:", reply_markup=ReplyKeyboardRemove())
            user_states[user_id] = "AWAIT_NOTIFY"
        else:
            update.message.reply_text("У вас нет прав для рассылки.")
    elif state == "AWAIT_NOTIFY":
        msg = text
        for uid in zones:
            bot.send_message(chat_id=int(uid), text=msg)
        update.message.reply_text("Рассылка выполнена.", reply_markup=main_menu_keyboard())
        user_states.pop(user_id, None)
    else:
        update.message.reply_text("Неизвестная команда.", reply_markup=main_menu_keyboard())

# Регистрируем хендлеры
dispatcher.add_handler(CommandHandler('start', start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

# --- Webhook ---
@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

@app.route("/")
def index():
    return "Бот работает"

if __name__ == '__main__':
    # Устанавливаем вебхук и запускаем Flask
    bot.set_webhook(f"{SELF_URL}/webhook")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000))
