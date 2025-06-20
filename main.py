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
# ensure no trailing whitespace or newlines
ZONES_CSV_URL = os.getenv("ZONES_CSV_URL", "").strip()
# mapping branches for All-users menu
# format: "Branch1=GID1,Branch2=GID2,..."
BRANCH_SHEETS_MAP = {
    k.strip(): int(v.strip())
    for k, v in (
        p.split("=", 1) for p in os.getenv("BRANCH_SHEETS_MAP", "").split(",") if "=" in p
    )
}

bot        = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)
user_states = {}   # user_id -> state

# --- Утилиты ---

def normalize_sheet_url(url: str, default_gid: int = 0) -> str:
    url = url.strip()
    if '/export' in url or url.endswith('.csv'):
        return url
    m = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    if m:
        sid = m.group(1)
        return f'https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={default_gid}'
    m2 = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if m2:
        fid = m2.group(1)
        return f'https://drive.google.com/uc?export=download&id={fid}'
    return url


def load_zones() -> dict:
    url = normalize_sheet_url(ZONES_CSV_URL)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text), dtype=str)
    df.columns = df.columns.str.strip().str.upper()
    required = {"ФИЛИАЛ", "РЭС", "ID", "ФИО"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"В файле зон не все колонки: {missing}")
    zones = {}
    for _, row in df.iterrows():
        uid = row["ID"].strip()
        zones[uid] = {
            "filial": row["ФИЛИАЛ"].strip(),
            "res":    row["РЭС"].strip(),
            "name":   row["ФИО"].strip(),
        }
    return zones

# --- Клавиатуры ---

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


def search_handler(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    zones = load_zones()
    user = zones.get(user_id)
    if not user:
        update.message.reply_text("У вас нет прав доступа.", reply_markup=main_menu_keyboard())
        return
    filial = user['filial']
    # если All — показываем кнопки для всех филиалов
    if filial.lower() == 'all':
        buttons = [[b] for b in BRANCH_SHEETS_MAP.keys()]
        buttons.append(["Назад"])
        update.message.reply_text(
            "Выберите филиал:",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
        )
        user_states[user_id] = 'CHOOSING_BRANCH'
    else:
        # конкретный филиал — сразу просим ТП
        update.message.reply_text(
            f"Введите номер ТП для филиала {filial}:",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
        )
        user_states[user_id] = 'ENTERING_TP'


dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(Filters.regex('^Поиск$'), search_handler))

# --- Вебхук ---

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return 'OK'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
