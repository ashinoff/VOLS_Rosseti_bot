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
user_data   = {}   # user-specific data like chosen branch

# --- Utilities ---
def normalize_sheet_url(url: str, default_gid: int = 0) -> str:
    url = url.strip()
    if '/export' in url or url.endswith('.csv'):
        return url
    m = re.search(r'/d/([\w-]+)', url)
    if m:
        sid = m.group(1)
        return f'https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={default_gid}'
    m2 = re.search(r'/file/d/([\w-]+)', url)
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

# --- Keyboards ---
def main_menu():
    return ReplyKeyboardMarkup(
        [["Поиск"], ["Справочная информация"], ["Уведомление всем"]],
        resize_keyboard=True
    )

def branches_keyboard():
    buttons = [[b] for b in BRANCH_SHEETS_MAP]
    buttons.append(["Назад"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# --- Handlers ---

def start(update: Update, context: CallbackContext):
    uid = str(update.message.from_user.id)
    name = load_zones().get(uid, {}).get('name', 'пользователь')
    update.message.reply_text(f"Здравствуйте, {name}!", reply_markup=main_menu())
    user_states.pop(uid, None)
    user_data.pop(uid, None)


def search(update: Update, context: CallbackContext):
    uid = str(update.message.from_user.id)
    zones = load_zones()
    user = zones.get(uid)
    if not user:
        update.message.reply_text("У вас нет прав доступа.", reply_markup=main_menu())
        return
    if user['filial'].lower() == 'all':
        update.message.reply_text("Выберите филиал:", reply_markup=branches_keyboard())
        user_states[uid] = 'CHOOSE_BRANCH'
    else:
        user_data[uid] = {'filial': user['filial'], 'gid': BRANCH_SHEETS_MAP.get(user['filial'], 0)}
        update.message.reply_text(
            f"Введите номер ТП для филиала {user['filial']}:",
            reply_markup=ReplyKeyboardMarkup([["Назад"]], resize_keyboard=True)
        )
        user_states[uid] = 'ENTER_TP'


def text_handler(update: Update, context: CallbackContext):
    uid = str(update.message.from_user.id)
    state = user_states.get(uid)
    text = update.message.text.strip()

    if state == 'CHOOSE_BRANCH':
        if text == 'Назад':
            start(update, context)
            return
        if text in BRANCH_SHEETS_MAP:
            user_data[uid] = {'filial': text, 'gid': BRANCH_SHEETS_MAP[text]}
            update.message.reply_text(
                f"Введите номер ТП для филиала {text}:",
                reply_markup=ReplyKeyboardMarkup([["Назад"]], resize_keyboard=True)
            )
            user_states[uid] = 'ENTER_TP'
        else:
            update.message.reply_text("Неверный выбор, выберите филиал.", reply_markup=branches_keyboard())
    elif state == 'ENTER_TP':
        if text == 'Назад':
            search(update, context)
            return
        # дальше логика поиска по введенному TP: загрузить файл филиала по gid и искать
        # ... ваш код тут ...
        update.message.reply_text("Результаты поиска ТП (здесь ваш результат)", reply_markup=main_menu())
        user_states.pop(uid, None)
        user_data.pop(uid, None)
    else:
        update.message.reply_text("Пожалуйста, воспользуйтесь меню.", reply_markup=main_menu())

# регистрация хендлеров
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(Filters.regex('^Поиск$'), search))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, text_handler))

# --- Webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    upd = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(upd)
    return 'OK'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
