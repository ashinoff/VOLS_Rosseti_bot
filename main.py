import os
import re
from io import StringIO

import pandas as pd
import requests
from flask import Flask, request
from telegram import Bot, Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext

app = Flask(__name__)

# === ENV ===
TOKEN         = os.getenv("TOKEN")
SELF_URL      = os.getenv("SELF_URL")
ZONES_CSV_URL = os.getenv("ZONES_CSV_URL")
BRANCH_SHEETS_MAP = {
    k.strip(): v.strip()
    for k, v in (p.split("=", 1) for p in os.getenv("BRANCH_SHEETS_MAP", "").split(",") if "=" in p)
}

bot        = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)
user_states = {}   # user_id -> state

# --- Утилиты ---

def normalize_sheet_url(url: str, default_gid: int = 0) -> str:
    """
    Преобразует ссылку Google Sheets или Google Drive на CSV-документ в прямую ссылку для скачивания CSV.
    """
    # если уже корректный экспорт
    if '/export' in url or url.startswith('https://') and url.endswith('.csv'):
        return url
    # Google Sheets URL
    m = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    if m:
        sheet_id = m.group(1)
        return f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={default_gid}'
    # Google Drive file URL
    m2 = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if m2:
        file_id = m2.group(1)
        return f'https://drive.google.com/uc?export=download&id={file_id}'
    return url


def load_zones() -> dict:
    """Загружает CSV-файл зон доступа и возвращает словарь по ID пользователя."""
    url = normalize_sheet_url(ZONES_CSV_URL)
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    # читаем CSV
    df = pd.read_csv(StringIO(response.text), dtype=str)
    required = {"Филиал", "РЭС", "ID", "ФИО"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"В файле зон не все колонки присутствуют: {missing}")
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
    return ReplyKeyboardMarkup(
        [["Поиск"], ["Справочная информация"], ["Уведомление всем"]],
        resize_keyboard=True
    )

# --- Хендлеры ---

def start(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    name = load_zones().get(user_id, {}).get('name', 'пользователь')
    update.message.reply_text(
        f"Здравствуйте, {name}!",
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
        return

    if state == "AWAIT_NUMBER":
        update.message.reply_text(
            f"Запрос принят для номера {text}. Выберите тип информации.",
            reply_markup=ReplyKeyboardMarkup(
                [["Договор"], ["Адрес подключения"], ["Прибор учёта"], ["Назад"]],
                resize_keyboard=True
            )
        )
        user_states[user_id] = ("AWAIT_INFO", text)
        return

    if isinstance(state, tuple) and state[0] == "AWAIT_INFO":
        _, number = state
        info_type = text
        update.message.reply_text(f"Информация ({info_type}) по {number}: ...")
        update.message.reply_text("Возвращаемся в главное меню.", reply_markup=main_menu_keyboard())
        user_states.pop(user_id, None)
        return

    if text == "Справочная информация":
        update.message.reply_text(
            "Выберите раздел:",
            reply_markup=ReplyKeyboardMarkup(
                [["Сечение кабеля (ток, мощность)"], ["Номиналы ВА (ток, мощность)"], ["Формулы"], ["Назад"]],
                resize_keyboard=True
            )
        )
        user_states[user_id] = "AWAIT_HELP"
        return

    if state == "AWAIT_HELP":
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
        return

    if text == "Уведомление всем":
        if zones.get(user_id, {}).get('res') == 'admin':
            update.message.reply_text("Введите сообщение для рассылки:", reply_markup=ReplyKeyboardRemove())
            user_states[user_id] = "AWAIT_NOTIFY"
        else:
            update.message.reply_text("У вас нет прав для рассылки.")
        return

    if state == "AWAIT_NOTIFY":
        msg = text
        for uid in zones:
            bot.send_message(chat_id=int(uid), text=msg)
        update.message.reply_text("Рассылка выполнена.", reply_markup=main_menu_keyboard())
        user_states.pop(user_id, None)
        return

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
    bot.set_webhook(f"{SELF_URL}/webhook")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')))
