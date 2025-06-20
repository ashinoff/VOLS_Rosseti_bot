import os
import threading
import time
import re
import requests
import pandas as pd
from flask import Flask, request, jsonify
from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext

# === ENVIRONMENT VARIABLES ===
# TOKEN       - Telegram Bot Token
# SELF_URL    - URL of this service (for self-ping)
# ZONES_CSV_URL - Google Sheets CSV export URL with zones data
TOKEN = os.getenv("TOKEN")
SELF_URL = os.getenv("SELF_URL", "").rstrip('/')
ZONES_CSV_URL = os.getenv("ZONES_CSV_URL", "").strip()

# === HARD-CODED BRANCH BUTTONS ===
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

# === FLASK & TELEGRAM SETUP ===
app = Flask(__name__)
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# === HELPER FUNCTIONS ===

def normalize_sheet_url(url: str) -> str:
    # Convert Google Sheets/Drive link to direct CSV download
    if '/export' in url or url.endswith('.csv'):
        return url
    m = re.search(r'/d/([\w-]+)', url)
    if m:
        sid = m.group(1)
        return f'https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid=0'
    m2 = re.search(r'/file/d/([\w-]+)', url)
    if m2:
        fid = m2.group(1)
        return f'https://drive.google.com/uc?export=download&id={fid}'
    return url


def load_zones() -> dict:
    # Load zones CSV into dict: {user_id: filial}
    url = normalize_sheet_url(ZONES_CSV_URL)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    df = pd.read_csv(pd.compat.StringIO(resp.text))
    df.columns = df.columns.str.strip().str.upper()
    required = {'ФИЛИАЛ', 'РЭС', 'ID', 'ФИО'}
    if not required.issubset(df.columns):
        raise ValueError(f"Missing columns in zones file: {required - set(df.columns)}")
    return {int(row['ID']): row['ФИЛИАЛ'] for _, row in df.iterrows()}

# === KEYBOARDS ===
def main_menu_keyboard():
    return ReplyKeyboardMarkup([['Поиск']], resize_keyboard=True)

def branches_keyboard():
    keys = [[b] for b in BRANCHES]
    keys.append(['Назад'])
    return ReplyKeyboardMarkup(keys, resize_keyboard=True)

# === HANDLERS ===

def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Добро пожаловать! Нажмите 'Поиск' для выбора филиала.",
        reply_markup=main_menu_keyboard()
    )

def handle_search(update: Update, context: CallbackContext):
    # Determine user zone and show branches if 'All'
    user_id = update.message.from_user.id
    zones = load_zones()
    filial = zones.get(user_id)
    if filial == 'All':
        update.message.reply_text(
            "Выберите филиал:", reply_markup=branches_keyboard()
        )
    elif filial in BRANCHES:
        update.message.reply_text(
            f"Поиск будет выполняться для филиала {filial}."
        )
    else:
        update.message.reply_text("У вас нет прав доступа.")

# === SELF-PING TO KEEP ALIVE ===
def ping_self():
    if not SELF_URL:
        return
    while True:
        try:
            requests.get(SELF_URL + '/webhook')
        except:
            pass
        time.sleep(300)

# Register handlers

dispatcher.add_handler(CommandHandler('start', start))
dispatcher.add_handler(MessageHandler(Filters.regex('^Поиск$'), handle_search))

# === WEBHOOK ===
@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return jsonify({'status': 'ok'})

# === RUN ===
if __name__ == '__main__':
    threading.Thread(target=ping_self, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
