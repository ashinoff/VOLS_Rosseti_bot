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
# TOKEN           - Telegram Bot Token
# SELF_URL        - URL of this service (for self-ping)
# ZONES_CSV_URL   - Google Sheets CSV export URL с вашими зонами
TOKEN = os.getenv("TOKEN")
SELF_URL = os.getenv("SELF_URL", "").rstrip('/')
ZONES_CSV_URL = os.getenv("ZONES_CSV_URL", "").strip()

# === ЖЁСТКО ЗАПИСАННЫЕ КНОПКИ ===
BRANCHES = [
    "Краснодарские ЭС",
    "Сочинские ЭС",
    "Юго-Западные ЭС",
    "Адыгейские ЭС",
    "Армавирские ЭС",
    "Лабинские ЭС",
    "Ленинградские ЭС",
    "Славянские ЭС",
    "Тимашевские ЭС",
    "Тихорецкие ЭС",
    "Усть-Лабинские ЭС",
]

# === FLASK & TELEGRAM SETUP ===
app = Flask(__name__)
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# === HELPERS ===
def normalize_sheet_url(url: str) -> str:
    if '/export' in url or url.endswith('.csv'):
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

def load_zones() -> dict:
    url = normalize_sheet_url(ZONES_CSV_URL)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    df = pd.read_csv(pd.compat.StringIO(resp.text), header=None, skiprows=1,
                     usecols=[0,1,2,3], names=['filial','res','id','fio'])
    return {int(row['id']): row for _, row in df.iterrows()}

def load_branch_sheet(branch_name: str) -> pd.DataFrame:
    url = os.getenv(f"URL_{branch_name.replace(' ', '_')}", "")
    url = normalize_sheet_url(url)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    # читаем с заголовком первой строки
    return pd.read_csv(pd.compat.StringIO(resp.text))

# === KEYBOARDS ===
def main_menu():
    return ReplyKeyboardMarkup([['Поиск'], ['Справка']], resize_keyboard=True)

def branches_kb():
    keys = [[b] for b in BRANCHES]
    keys.append(['Назад'])
    return ReplyKeyboardMarkup(keys, resize_keyboard=True)

def search_kb():
    return ReplyKeyboardMarkup([['Поиск по ТП'], ['Назад']], resize_keyboard=True)

# === HANDLERS ===
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Приветствую Вас " + load_zones().get(update.message.from_user.id, {}).get('fio', '') ,
        reply_markup=main_menu()
    )

def handle_search(update: Update, context: CallbackContext):
    user = update.message.from_user.id
    zones = load_zones()
    info = zones.get(user)
    if not info:
        update.message.reply_text("У вас нет прав доступа.")
        return
    filial = info['filial']
    if filial == 'All':
        update.message.reply_text("Выбор филиала:", reply_markup=branches_kb())
        context.user_data['state'] = 'choose_branch'
    elif filial in BRANCHES:
        context.user_data['branch'] = filial
        update.message.reply_text(f"Выбран филиал {filial}\nЧто дальше?", reply_markup=search_kb())
        context.user_data['state'] = 'in_branch'
    else:
        update.message.reply_text("У вас нет прав доступа.")

def handle_branch_choice(update: Update, context: CallbackContext):
    text = update.message.text
    if text == 'Назад':
        update.message.reply_text("Поиск отменён.", reply_markup=main_menu())
        context.user_data.clear()
    elif text in BRANCHES:
        context.user_data['branch'] = text
        update.message.reply_text(f"Выбран филиал {text}\nЧто дальше?", reply_markup=search_kb())
        context.user_data['state'] = 'in_branch'
    else:
        update.message.reply_text("Выберите филиал или Назад.")

def handle_tp_menu(update: Update, context: CallbackContext):
    text = update.message.text
    if text == 'Назад':
        # возвращаемся к меню филиалов
        update.message.reply_text("Выбор филиала:", reply_markup=branches_kb())
        context.user_data['state'] = 'choose_branch'
    elif text == 'Поиск по ТП':
        update.message.reply_text("Введите номер ТП:", reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        context.user_data['state'] = 'await_tp'
    else:
        update.message.reply_text("Нажмите Поиск по ТП или Назад.")

def handle_tp_search(update: Update, context: CallbackContext):
    tp_input = update.message.text.strip()
    if tp_input == 'Назад':
        # обратно к выбору действий в филиале
        b = context.user_data.get('branch')
        update.message.reply_text(f"Выбран филиал {b}\nЧто дальше?", reply_markup=search_kb())
        context.user_data['state'] = 'in_branch'
        return

    branch = context.user_data.get('branch')
    try:
        df = load_branch_sheet(branch)
    except Exception as e:
        update.message.reply_text(f"Ошибка загрузки таблицы {branch}: {e}")
        return

    # поиск по колонке D – 'Наименование ТП'
    mask = df['Наименование ТП'].str.upper().str.contains(tp_input.upper())
    found = df[mask]
    cnt = len(found)
    if cnt == 0:
        update.message.reply_text(f"ТП {tp_input} в филиале {branch} не найден.")
    else:
        lines = [f"Найдено {cnt} ВОЛС с договором аренды:",
                 f"{tp_input.upper()} находится в {branch} РЭС"]
        for _, row in found.iterrows():
            lines.append(
                f"ВЛ {row['Наименование ВЛ']}: Опоры: {row['Опоры']}, Кол-во опор: {row['Количество опор']}, Провайдер: {row['Наименование Провайдера']}"
            )
        update.message.reply_text("\n".join(lines),
                                  reply_markup=branches_kb())
        context.user_data['state'] = 'choose_branch'

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
dispatcher.add_handler(MessageHandler(Filters.regex('^(' + '|'.join(BRANCHES) + '|Назад)$'), handle_branch_choice))
dispatcher.add_handler(MessageHandler(Filters.regex('^(Поиск по ТП|Назад)$'), handle_tp_menu))
dispatcher.add_handler(MessageHandler(Filters.text & Filters.user(username='*'), handle_tp_search))

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
