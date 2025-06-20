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
# TOKEN                  — ваш Telegram Bot Token
# SELF_URL               — URL этого сервиса (для self-ping)
# ZONES_CSV_URL          — ссылка на CSV-таблицу зон доступа
# ИД каждой филиальной таблицы (любые названия ENV, главное потом в коде ими пользоваться):
# YUGO_ZAPAD_ES_URL      
# UST_LAB_ES_URL         
# TIMASHEV_ES_URL        
# TIKHORETS_ES_URL       
# SOCH_ES_URL            
# SLAV_ES_URL            
# LENINGRAD_ES_URL       
# LABIN_ES_URL           
# KRASN_ES_URL           
# ARMAVIR_ES_URL         
# ADYGEA_ES_URL          
TOKEN = os.getenv("TOKEN")
SELF_URL = os.getenv("SELF_URL", "").rstrip('/')
ZONES_CSV_URL = os.getenv("ZONES_CSV_URL", "").strip()

# === BRANCHES ===
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
    "Ленинградские ЭС",
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
    text = resp.content.decode('utf-8-sig')
    df = pd.read_csv(StringIO(text), header=None, skiprows=1)
    zones_map, names_map = {}, {}
    for _, row in df.iterrows():
        try:
            uid = int(row[2])
            zones_map[uid] = str(row[0]).strip()
            names_map[uid] = str(row[3]).strip()
        except:
            continue
    return zones_map, names_map

# === KEYBOARDS ===
def main_menu_keyboard(filial: str = None):
    # для всех — кнопка «Поиск», для All — «Выбор филиала»
    label = "Выбор филиала" if filial == "All" else "Поиск"
    return ReplyKeyboardMarkup([[label]], resize_keyboard=True)

def branches_keyboard():
    keys = [[b] for b in BRANCHES]
    keys.append(['Назад'])
    return ReplyKeyboardMarkup(keys, resize_keyboard=True)

def search_tp_keyboard():
    return ReplyKeyboardMarkup([['Поиск по ТП'], ['Назад']], resize_keyboard=True)

# === HANDLERS ===
def start(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    try:
        zones, names = load_zones()
    except Exception as e:
        update.message.reply_text(f"Ошибка загрузки прав доступа: {e}")
        return

    filial = zones.get(user_id)
    name = names.get(user_id)
    if name:
        greeting = f'Приветствую Вас, {name}!'
    else:
        greeting = 'Добро пожаловать!'

    update.message.reply_text(
        f"{greeting} Нажмите «{'Выбор филиала' if filial=='All' else 'Поиск'}».",
        reply_markup=main_menu_keyboard(filial)
    )

def handle_search_or_choose(update: Update, context: CallbackContext):
    text = update.message.text
    user_id = update.message.from_user.id
    zones, _ = load_zones()
    filial = zones.get(user_id)

    # если All и нажали «Выбор филиала»
    if filial == 'All' and text == 'Выбор филиала':
        update.message.reply_text("Выберите филиал:", reply_markup=branches_keyboard())
        return

    # для обычных пользователей «Поиск»
    if filial != 'All' and text == 'Поиск':
        update.message.reply_text(f"Поиск будет выполняться для филиала {filial}.",
                                  reply_markup=search_tp_keyboard())
        return

    # выбор филиала из списка
    if text in BRANCHES:
        context.user_data['branch'] = text
        update.message.reply_text(f"Выбран филиал «{text}». Что дальше?",
                                  reply_markup=search_tp_keyboard())
        return

    # «Поиск по ТП»
    if text == 'Поиск по ТП':
        branch = context.user_data.get('branch')
        update.message.reply_text(f"Введите номер ТП для филиала «{branch}»:",
                                  reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    # «Назад»
    if text == 'Назад':
        start(update, context)
        return

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
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_search_or_choose))

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
