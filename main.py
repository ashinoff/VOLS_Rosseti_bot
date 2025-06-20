import os
import threading
import time
import re
import requests
import pandas as pd
from io import StringIO
from flask import Flask, request, jsonify
from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext

# === ENVIRONMENT VARIABLES ===
TOKEN = os.getenv("TOKEN")
SELF_URL = os.getenv("SELF_URL", "").rstrip('/')
ZONES_CSV_URL = os.getenv("ZONES_CSV_URL", "").strip()

# === BRANCH BUTTONS ===
BRANCHES = [
    "Краснодарские ЭС",
    "Сочинские ЭС",
    "Юго-Западные ЭС",
    "Адыгейские ЭС",
    "Тихорецкие ЭС",
    "Армавирские ЭС",
    "Усть-Лабинские ЭС",
    "Тимашевские ЭС",
    "Славянские ЭС",
    "Лабинские ЭС",
]

# === SETUP ===
app = Flask(__name__)
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# === HELPERS ===
def normalize_sheet_url(url: str) -> str:
    if 'output=csv' in url or '/export' in url or url.endswith('.csv'):
        return url
    m = re.search(r'/d/e/([\w-]+)/', url)
    if m:
        sid = m.group(1)
        return f'https://docs.google.com/spreadsheets/d/e/{sid}/export?format=csv&gid=0'
    m = re.search(r'/d/([\w-]+)', url)
    if m:
        sid = m.group(1)
        return f'https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid=0'
    m2 = re.search(r'/file/d/([\w-]+)', url)
    if m2:
        fid = m2.group(1)
        return f'https://drive.google.com/uc?export=download&id={fid}'
    return url

def load_zones() -> (dict, dict):
    url = normalize_sheet_url(ZONES_CSV_URL)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    text = resp.content.decode('utf-8-sig')  # принудительно в UTF-8, отрезая BOM
    df = pd.read_csv(StringIO(text), header=None, skiprows=1)
    if df.shape[1] < 4:
        raise ValueError(f"В таблице должно быть минимум 4 колонки, найдено {df.shape[1]}")
    zones_map, names_map = {}, {}
    for _, row in df.iterrows():
        try:
            uid = int(row[2])
        except:
            continue
        zones_map[uid] = str(row[0]).strip()
        names_map[uid] = str(row[3]).strip()
    return zones_map, names_map

# === KEYBOARDS ===
def main_menu_keyboard():
    return ReplyKeyboardMarkup([['Поиск']], resize_keyboard=True)

def branches_keyboard():
    keys = [[b] for b in BRANCHES]
    keys.append(['Назад'])
    return ReplyKeyboardMarkup(keys, resize_keyboard=True)

# === HANDLERS ===
def start(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    try:
        zones, names = load_zones()
    except Exception as e:
        update.message.reply_text(f"Ошибка загрузки прав доступа: {e}")
        return

    name = names.get(user_id)
    if name:
        greeting = f'Приветствую Вас, "{name}"! Нажмите «Поиск» для выбора филиала.'
    else:
        greeting = 'Добро пожаловать! Нажмите «Поиск» для выбора филиала.'
    update.message.reply_text(greeting, reply_markup=main_menu_keyboard())

def handle_search(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    try:
        zones, _ = load_zones()
    except Exception as e:
        update.message.reply_text(f"Ошибка загрузки прав доступа: {e}")
        return

    filial = zones.get(user_id)
    if filial == 'All':
        update.message.reply_text("Выберите филиал:", reply_markup=branches_keyboard())
    elif filial in BRANCHES:
        update.message.reply_text(f"Поиск будет выполняться для филиала {filial}.")
    else:
        update.message.reply_text("У вас нет прав доступа.")

# === SELF-PING ===
def ping_self():
    if not SELF_URL:
        return
    while True:
        try:
            requests.get(SELF_URL + '/webhook')
        except:
            pass
        time.sleep(300)

# === REGISTER ===
dispatcher.add_handler(CommandHandler('start', start))
dispatcher.add_handler(MessageHandler(Filters.regex('^Поиск$'), handle_search))

# === WEBHOOK ===
@app.route('/webhook', methods=['POST'])
def webhook():
    upd = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(upd)
    return jsonify({'status': 'ok'})

# === MAIN ===
if __name__ == '__main__':
    threading.Thread(target=ping_self, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
