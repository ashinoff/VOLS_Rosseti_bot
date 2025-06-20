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

# === Филиальные таблицы ===
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
    zones, res_zones, names = {}, {}, {}
    for _, row in df.iterrows():
        try:
            uid = int(row[2])
        except:
            continue
        zones[uid]     = str(row[0]).strip()  # Филиал или "All"
        res_zones[uid] = str(row[1]).strip()  # РЭС или "All"
        names[uid]     = str(row[3]).strip()  # ФИО
    return zones, res_zones, names

# === KEYBOARDS ===
def main_menu_keyboard(is_all=False):
    return ReplyKeyboardMarkup(
        [[ "Выбор филиала" if is_all else "Поиск" ]],
        resize_keyboard=True
    )

def branches_keyboard():
    keys = [[b] for b in BRANCHES]
    keys.append(["Назад"])
    return ReplyKeyboardMarkup(keys, resize_keyboard=True)

def next_actions_keyboard():
    return ReplyKeyboardMarkup(
        [["Поиск ТП"], ["Выбор филиала"]],
        resize_keyboard=True
    )

# === HANDLERS ===
def start(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    try:
        branch_zones, res_zones, names = load_zones()
    except Exception as e:
        return update.message.reply_text(f"Ошибка загрузки прав доступа: {e}")

    if user_id not in branch_zones:
        return update.message.reply_text(
            "К сожалению, у вас нет доступа, обратитесь к администратору."
        )

    user_branch = branch_zones[user_id]
    name        = names[user_id]
    greet       = f"Приветствую Вас, {name}!" if name else "Добро пожаловать!"
    update.message.reply_text(
        f"{greet} Нажмите «{'Выбор филиала' if user_branch=='All' else 'Поиск'}».",
        reply_markup=main_menu_keyboard(user_branch=='All')
    )
    context.user_data.clear()
    context.user_data['branch'] = None

def handle_text(update: Update, context: CallbackContext):
    text    = update.message.text.strip()
    user_id = update.message.from_user.id

    try:
        branch_zones, res_zones, names = load_zones()
    except Exception as e:
        return update.message.reply_text(f"Ошибка загрузки прав доступа: {e}")

    if user_id not in branch_zones:
        return update.message.reply_text(
            "К сожалению, у вас нет доступа, обратитесь к администратору."
        )

    user_branch = branch_zones[user_id]
    user_res    = res_zones[user_id]
    name        = names[user_id]

    # Кнопка «Выбор филиала»
    if text == "Выбор филиала":
        if user_branch == "All":
            return update.message.reply_text(
                "Выберите филиал:", reply_markup=branches_keyboard()
            )
        else:
            return update.message.reply_text(
                f"{name}, вы можете просматривать только филиал {user_branch}.",
                reply_markup=next_actions_keyboard()
            )

    # Кнопка «Поиск»
    if text == "Поиск":
        if user_branch == "All":
            return update.message.reply_text(
                "Выберите филиал:", reply_markup=branches_keyboard()
            )
        else:
            context.user_data['branch'] = user_branch
            return update.message.reply_text(
                f"{name}, введите номер ТП.", reply_markup=next_actions_keyboard()
            )

    # Выбор филиала из списка All
    if text in BRANCHES:
        context.user_data['branch'] = text
        return update.message.reply_text(
            f"{name}, введите номер ТП.", reply_markup=next_actions_keyboard()
        )

    # Кнопка «Поиск ТП»
    if text == "Поиск ТП" and context.user_data.get('branch'):
        return update.message.reply_text(
            f"{name}, введите номер ТП.", reply_markup=next_actions_keyboard()
        )

    # Обработка ввода ТП
    if context.user_data.get('branch'):
        branch    = context.user_data['branch']
        sheet_url = BRANCH_URLS.get(branch)
        if not sheet_url:
            return update.message.reply_text(
                "Не задана таблица для этого филиала.",
                reply_markup=next_actions_keyboard()
            )
        try:
            csv_url = normalize_sheet_url(sheet_url)
            r       = requests.get(csv_url, timeout=10); r.raise_for_status()
            df      = pd.read_csv(StringIO(r.content.decode('utf-8-sig')))
        except Exception as e:
            return update.message.reply_text(
                f"Ошибка загрузки таблицы {branch}: {e}",
                reply_markup=next_actions_keyboard()
            )

        tp_input = text.upper().replace("ТП-", "").strip()
        df['D_UP'] = df['Наименование ТП'].str.upper().str.replace("ТП-", "")
        found = df[df['D_UP'].str.contains(tp_input, na=False)]

        if found.empty:
            resp = "Договоров ВОЛС на данной ТП нет, либо название ТП введено некорректно."
        else:
            tp_name = found.iloc[0]['Наименование ТП']
            count   = len(found)
            lines   = [
                f"Найдено {count} ВОЛС с договором аренды:",
                f"{tp_name} находится в {found.iloc[0]['РЭС']}"
            ]
            for _, row in found.iterrows():
                lines.append("")  # пустая строка
                lines.append(f"ВЛ {row['Наименование ВЛ']}:")
                lines.append(f"Опоры: {row['Опоры']}")
                lines.append(f"Кол-во опор: {row['Количество опор']}")
                lines.append(f"Провайдер: {row['Наименование Провайдера']}")
            resp = "\n".join(lines)

        update.message.reply_text(resp)
        return update.message.reply_text(
            f"{name}, задание выполнено.",
            reply_markup=next_actions_keyboard()
        )

    # Во всех остальных случаях
    return update.message.reply_text(
        "Нажмите одну из кнопок меню.",
        reply_markup=main_menu_keyboard(user_branch=='All')
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

# === РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ===
dispatcher.add_handler(CommandHandler('start', start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

# === WEBHOOK ===
@app.route('/webhook', methods=['POST'])
def webhook():
    upd = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(upd)
    return jsonify({'status': 'ok'})

# === START APP ===
if __name__ == '__main__':
    threading.Thread(target=ping_self, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
