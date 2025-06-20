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
# TOKEN           - Telegram Bot Token
# SELF_URL        - URL of this service (for self-ping), без конца "/"
# ZONES_CSV_URL   - Google Sheets CSV export URL with zones data
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

# === HELPERS ===

def normalize_sheet_url(url: str) -> str:
    """
    Convert various Google Sheets/Drive URLs to a direct CSV download link.
    Если в URL уже стоит output=csv или /export или это .csv — возвращаем как есть.
    Иначе собираем прямой экспорт.
    """
    if 'output=csv' in url or '/export' in url or url.endswith('.csv'):
        return url
    # Published sheet (public) URLs
    m = re.search(r'/d/e/([\w-]+)/', url)
    if m:
        sid = m.group(1)
        return f'https://docs.google.com/spreadsheets/d/e/{sid}/export?format=csv&gid=0'
    # Standard sheet link
    m = re.search(r'/d/([\w-]+)', url)
    if m:
        sid = m.group(1)
        return f'https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid=0'
    # Drive file link
    m2 = re.search(r'/file/d/([\w-]+)', url)
    if m2:
        fid = m2.group(1)
        return f'https://drive.google.com/uc?export=download&id={fid}'
    return url

def load_zones() -> dict:
    """
    Загружает CSV из ZONES_CSV_URL и возвращает словарь user_id -> filial.
    Ожидаемые колонки в CSV: ФИЛИАЛ, РЭС, ID, ФИО
    """
    url = normalize_sheet_url(ZONES_CSV_URL)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    # Читаем как CSV
    df = pd.read_csv(StringIO(resp.text))
    df.columns = df.columns.str.strip().str.upper()
    required = {'ФИЛИАЛ', 'РЭС', 'ID', 'ФИО'}
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        raise ValueError(f"Отсутствуют колонки: {missing}")
    # Преобразуем в dict: ID (int) -> ФИЛИАЛ
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
    user_id = update.message.from_user.id
    try:
        zones = load_zones()
    except Exception as e:
        update.message.reply_text(f"Ошибка чтения прав доступа: {e}")
        return

    filial = zones.get(user_id)
    if filial == 'All':
        update.message.reply_text("Выберите филиал:", reply_markup=branches_keyboard())
    elif filial in BRANCHES:
        update.message.reply_text(f"Поиск будет выполняться для филиала {filial}.")
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

# === REGISTER HANDLERS ===

dispatcher.add_handler(CommandHandler('start', start))
dispatcher.add_handler(MessageHandler(Filters.regex('^Поиск$'), handle_search))

# === WEBHOOK ROUTE ===

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return jsonify({'status': 'ok'})

# === RUN APPLICATION ===

if __name__ == '__main__':
    # Запускаем самопинг для удержания
    threading.Thread(target=ping_self, daemon=True).start()
    # Запускаем Flask
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
