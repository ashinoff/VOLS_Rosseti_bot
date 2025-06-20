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
TOKEN         = os.getenv("TOKEN")
SELF_URL      = os.getenv("SELF_URL", "").rstrip('/')
ZONES_CSV_URL = os.getenv("ZONES_CSV_URL", "").strip()

# Филиальные таблицы
BRANCH_URLS = {
    "Юго-Западные ЭС":   os.getenv("YUGO_ZAPAD_ES_URL", ""),
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

BRANCHES = list(BRANCH_URLS.keys())

# === SETUP ===
app        = Flask(__name__)
bot        = Bot(token=TOKEN)
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
    r   = requests.get(url, timeout=10); r.raise_for_status()
    text = r.content.decode('utf-8-sig')
    df   = pd.read_csv(StringIO(text), header=None, skiprows=1)
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
    label = "Выбрать филиал" if is_all else "Поиск по ТП"
    return ReplyKeyboardMarkup([[label]], resize_keyboard=True)

def branches_keyboard():
    keys = [[b] for b in BRANCHES]
    return ReplyKeyboardMarkup(keys, resize_keyboard=True)

def search_tp_keyboard(branch):
    # после поиска оставляем две кнопки: новый поиск и смена филиала
    return ReplyKeyboardMarkup(
        [["Поиск по ТП"], ["Выбрать филиал"]],
        resize_keyboard=True
    )

# === HANDLERS ===
def start(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    try:
        zones, names = load_zones()
    except Exception as e:
        return update.message.reply_text(f"Ошибка загрузки прав доступа: {e}")
    filial = zones.get(user_id)
    name   = names.get(user_id)
    if filial == "All":
        greet = f"Приветствую Вас, {name}! Вы можете осуществлять поиск в любом филиале."
        update.message.reply_text(
            greet,
            reply_markup=main_menu_keyboard(is_all=True)
        )
    else:
        # если конкретный филиал
        greet = f"Приветствую Вас, {name}! Вы можете просматривать только филиал {filial}."
        update.message.reply_text(
            greet,
            reply_markup=main_menu_keyboard(is_all=False)
        )
        context.user_data['branch'] = filial
    context.user_data.clear()
    return

def handle_text(update: Update, context: CallbackContext):
    text    = update.message.text.strip()
    user_id = update.message.from_user.id
    zones, names = load_zones()
    filial  = zones.get(user_id)
    name    = names.get(user_id)

    # 1) All / All: кнопка "Выбрать филиал"
    if filial == "All":
        # выбор филиала
        if text == "Выбрать филиал":
            return update.message.reply_text(
                "Выберите филиал:",
                reply_markup=branches_keyboard()
            )
        # выбран филиал из списка
        if text in BRANCHES:
            context.user_data['branch'] = text
            return update.message.reply_text(
                f"Выбран филиал {text}. Введите номер ТП:",
                reply_markup=search_tp_keyboard(text)
            )
        # новый поиск по ТП
        if text == "Поиск по ТП" and context.user_data.get('branch'):
            branch = context.user_data['branch']
            return update.message.reply_text(
                "Введите номер ТП:",
                reply_markup=search_tp_keyboard(branch)
            )
    else:
        # 2 & 3) конкретный филиал
        branch = filial
        # кнопка смены филиала
        if text == "Выбрать филиал":
            return update.message.reply_text(
                f"{name}, вы можете просматривать только филиал {branch}.",
                reply_markup=search_tp_keyboard(branch)
            )
        # кнопка поиска по ТП
        if text == "Поиск по ТП":
            return update.message.reply_text(
                "Введите номер ТП:",
                reply_markup=search_tp_keyboard(branch)
            )

    # обработка ввода ТП
    if text and context.user_data.get('branch') and text not in ("Выбрать филиал", "Поиск по ТП"):
        branch = context.user_data['branch']
        # определяем РЭС-ограничение из zones: zones[user_id] может быть "Филиал" или "Филиал/РЭС"
        # но у нас zones хранит только филиал; чтобы прочитать РЭС, при загрузке CSV надо разделять колонку B
        # здесь считаем, что если zones[user_id] содержит '/', то после '/' идёт РЭС; иначе РЭС == All
        zone_str = filial
        # для простоты: если у пользователя задан филиал != All, то РЭС = All (зона 2),
        # иначе зона 1 мы уже обработали выше
        user_res = None
        if filial != "All":
            # загрузим raw zones df, чтобы получить колонку B
            url = normalize_sheet_url(ZONES_CSV_URL)
            r   = requests.get(url, timeout=10); r.raise_for_status()
            df_z = pd.read_csv(StringIO(r.content.decode('utf-8-sig')), header=None, skiprows=1)
            for _, row in df_z.iterrows():
                try:
                    if int(row[2]) == user_id:
                        user_res = str(row[1]).strip()
                        break
                except:
                    continue
        # теперь user_res == конкретный РЭС или "All"
        # если user_res задан != All, то зона 3, иначе зона 2

        # загрузка таблицы филиала
        sheet_url = BRANCH_URLS.get(branch)
        if not sheet_url:
            return update.message.reply_text("Не задана таблица для этого филиала.")
        try:
            csv_url = normalize_sheet_url(sheet_url)
            r       = requests.get(csv_url, timeout=10); r.raise_for_status()
            df      = pd.read_csv(StringIO(r.content.decode('utf-8-sig')))
        except Exception as e:
            return update.message.reply_text(f"Ошибка загрузки таблицы {branch}: {e}")

        # фильтрация по РЭС для зоны 3
        if user_res and user_res != "All":
            df = df[df['РЭС'] == user_res]

        # поиск ТП
        tp_input = text.upper().replace("ТП-", "").strip()
        df['D_UP'] = df['Наименование ТП'].str.upper().str.replace("ТП-", "", regex=False)
        found = df[df['D_UP'].str.contains(tp_input, na=False)]

        # если выходит за пределы доступа (зона 3) и строки пусты
        if user_res and user_res != "All" and found.empty:
            return update.message.reply_text(
                f"Задание на поиск относится к РЭС «{user_res}», к сожалению, у вас нет прав для просмотра."
            )
        # нет договоров или ввод некорректен
        if found.empty:
            return update.message.reply_text(
                "Договоров ВОЛС на данной ТП нет или введено некорректное название."
            )

        # успешный поиск (запрошено в зоне видимости)
        tp_name = found.iloc[0]['Наименование ТП']
        count   = len(found)
        lines = [f"Найдено {count} ВОЛС с договором аренды:",
                 f"{name}, задание выполнено!"]
        # вывод каждой ВЛ с разделением пустой строкой
        for _, row in found.iterrows():
            lines.append("")  # пустая строка-разделитель
            lines.append(f"ВЛ {row['Наименование ВЛ']}:")
            lines.append(f"Опоры: {row['Опоры']}")
            lines.append(f"Кол-во опор: {row['Количество опор']}")
            lines.append(f"Провайдер: {row['Наименование Провайдера']}")

        resp = "\n".join(lines)
        return update.message.reply_text(resp, reply_markup=search_tp_keyboard(branch))

    # пользователь не в списке
    return update.message.reply_text(
        "К сожалению, у вас нет доступа, обратитесь к администратору."
    )

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

# === REGISTRATION ===
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
