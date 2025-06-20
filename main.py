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

# Филиальные таблицы
BRANCH_URLS = {
    "Юго-Западные ЭС": os.getenv("YUGO_ZAPAD_ES_URL", ""),
    "Усть-Лабинские ЭС": os.getenv("UST_LAB_ES_URL", ""),
    "Тимашевские ЭС":    os.getenv("TIMASHEV_ES_URL", ""),
    "Тихорецкие ЭС":     os.getenv("TIKHORETS_ES_URL", ""),
    "Сочинские ЭС":      os.getenv("SOCH_ES_URL", ""),
    "Славянские ЭС":     os.getenv("SLAV_ES_URL", ""),
    "Ленинградские ЭС":  os.getenv("LENINGRAD_ES_URL", ""),
    "Лабинские ЭС":      os.getenv("LABIN_ES_URL", ""),
    "Краснодарские ЭС":  os.getenv("KRASN_ES_URL", ""),
    "Армавирские ЭС":    os.getenv("ARMAVIR_ES_URL", ""),
    "Адыгейские ЭС":     os.getenv("ADYGEA_ES_URL", ""),
}

# Список всех филиалов для кнопок
BRANCHES = list(BRANCH_URLS.keys())

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

def load_zones():
    url = normalize_sheet_url(ZONES_CSV_URL)
    r = requests.get(url, timeout=10); r.raise_for_status()
    text = r.content.decode('utf-8-sig')
    df = pd.read_csv(StringIO(text), header=None, skiprows=1)
    zones, names = {}, {}
    for _, row in df.iterrows():
        try:
            uid = int(row[2])
        except:
            continue
        zones[uid] = str(row[0]).strip()   # Филиал или "All"
        names[uid] = str(row[3]).strip()   # ФИО
    return zones, names

# === KEYBOARDS ===
def main_menu_keyboard(is_all=False):
    return ReplyKeyboardMarkup(
        [["Выбор филиала" if is_all else "Поиск"]],
        resize_keyboard=True
    )

def branches_keyboard():
    keys = [[b] for b in BRANCHES]
    keys.append(["Назад"])
    return ReplyKeyboardMarkup(keys, resize_keyboard=True)

def search_tp_keyboard():
    return ReplyKeyboardMarkup([["Поиск по ТП"], ["Назад"]], resize_keyboard=True)

def back_keyboard():
    return ReplyKeyboardMarkup([["Назад"]], resize_keyboard=True)

# === HANDLERS ===
def start(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    try:
        zones, names = load_zones()
    except Exception as e:
        return update.message.reply_text(f"Ошибка загрузки прав доступа: {e}")
    filial = zones.get(user_id)
    name = names.get(user_id)
    greet = f"Приветствую Вас, {name}!" if name else "Добро пожаловать!"
    update.message.reply_text(
        f"{greet} Нажмите «{'Выбор филиала' if filial=='All' else 'Поиск'}».",
        reply_markup=main_menu_keyboard(filial=='All')
    )
    # Сброс состояния
    context.user_data.clear()

def handle_text(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    user_id = update.message.from_user.id
    zones, _ = load_zones()
    filial = zones.get(user_id)

    # Назад
    if text == "Назад":
        # если были в режиме ожидания ТП — вернуться в меню поиска внутри филиала
        if context.user_data.get('await_tp'):
            context.user_data.pop('await_tp')
            return update.message.reply_text(
                f"Филиал «{context.user_data['branch']}».",
                reply_markup=search_tp_keyboard()
            )
        # если выбрали филиал и не вводили ТП — вернуться в выбор филиала или главное
        if context.user_data.get('branch'):
            context.user_data.pop('branch')
            return update.message.reply_text(
                "Выберите действие:",
                reply_markup=main_menu_keyboard(filial=='All')
            )
        # иначе старт
        return start(update, context)

    # Для All — выбор филиала
    if filial == "All" and text == "Выбор филиала":
        return update.message.reply_text("Выберите филиал:", reply_markup=branches_keyboard())

    # Пользователь без All — сразу поиск внутри своего филиала
    if filial != "All" and text == "Поиск":
        context.user_data['branch'] = filial
        context.user_data['await_tp'] = True
        return update.message.reply_text(
            f"Выбран филиал {filial}. Введите номер ТП:",
            reply_markup=back_keyboard()
        )

    # Выбор филиала из списка All
    if text in BRANCHES:
        context.user_data['branch'] = text
        context.user_data['await_tp'] = True
        return update.message.reply_text(
            f"Выбран филиал {text}. Введите номер ТП:",
            reply_markup=back_keyboard()
        )

    # Поиск по ТП (кнопка после выбора филиала)
    if text == "Поиск по ТП" and context.user_data.get('branch'):
        context.user_data['await_tp'] = True
        return update.message.reply_text(
            f"Введите номер ТП для филиала {context.user_data['branch']}:",
            reply_markup=back_keyboard()
        )

    # Обработка ввода номера ТП
    if context.user_data.get('await_tp') and context.user_data.get('branch'):
        branch = context.user_data['branch']
        sheet_url = BRANCH_URLS.get(branch)
        if not sheet_url:
            return update.message.reply_text("Не задана таблица для этого филиала.")
        # загрузить CSV
        try:
            csv_url = normalize_sheet_url(sheet_url)
            r = requests.get(csv_url, timeout=10); r.raise_for_status()
            df = pd.read_csv(StringIO(r.content.decode('utf-8-sig')))
        except Exception as e:
            return update.message.reply_text(f"Ошибка загрузки таблицы {branch}: {e}")

        # искать по «Наименование ТП»
        tp_input = text.upper().replace("ТП-", "").strip()
        df['D_UP'] = df['Наименование ТП'].str.upper().str.replace("ТП-", "")
        found = df[df['D_UP'].str.contains(tp_input, na=False)]

        if found.empty:
            resp = "Совпадений не найдено."
        else:
            lines = []
            for _, row in found.iterrows():
                lines.append(
                    f"{row['РЭС']} — ТП {row['Наименование ТП']}, "
                    f"ВЛ: {row['Наименование ВЛ']}, Опоры: {row['Опоры']}, "
                    f"Кол-во опор: {row['Количество опор']}, Провайдер: {row['Наименование Провайдера']}"
                )
            resp = f"Найдено {len(lines)}:\n" + "\n".join(lines)

        # отправить ответ и вернуться в меню поиска по ТП
        context.user_data.pop('await_tp')
        return update.message.reply_text(resp, reply_markup=search_tp_keyboard())

    # Любой другой ввод — ничего не понимаем
    return update.message.reply_text("Нажмите одну из кнопок меню.", reply_markup=main_menu_keyboard(filial=='All'))

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

# === РЕГИСТРАЦИЯ ===
dispatcher.add_handler(CommandHandler('start', start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

# === WEBHOOK ===
@app.route('/webhook', methods=['POST'])
def webhook():
    upd = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(upd)
    return jsonify({'status': 'ok'})

# === START ===
if __name__ == '__main__':
    threading.Thread(target=ping_self, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
