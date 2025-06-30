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
from telegram.error import BadRequest

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

app        = Flask(__name__)
bot        = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

def normalize_sheet_url(url: str) -> str:
    if 'output=csv' in url or '/export' in url or url.endswith('.csv'):
        return url
    m = re.search(r'/d/e/([\w-]+)/', url)
    if m:
        return f'https://docs.google.com/spreadsheets/d/e/{m.group(1)}/export?format=csv&gid=0'
    m = re.search(r'/d/([\w-]+)', url)
    if m:
        return f'https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid=0'
    m2 = re.search(r'/file/d/([\w-]+)', url)
    if m2:
        return f'https://drive.google.com/uc?export=download&id={m2.group(1)}'
    return url

def load_zones():
    url = normalize_sheet_url(ZONES_CSV_URL)
    r   = requests.get(url, timeout=10); r.raise_for_status()
    df  = pd.read_csv(StringIO(r.content.decode('utf-8-sig')), header=None, skiprows=1)
    bz, rz, names = {}, {}, {}
    for _, row in df.iterrows():
        try:
            uid = int(row[2])
        except:
            continue
        bz[uid]    = row[0].strip()
        rz[uid]    = row[1].strip()
        names[uid] = row[3].strip()
    return bz, rz, names

def kb_select_branch():
    return ReplyKeyboardMarkup([[b] for b in BRANCHES], resize_keyboard=True)

def kb_search_select():
    return ReplyKeyboardMarkup([["Поиск по ТП"], ["Выбор филиала"]], resize_keyboard=True)

def kb_only_select():
    return ReplyKeyboardMarkup([["Выбор филиала"]], resize_keyboard=True)

def kb_ambiguous(options):
    keys = [[opt] for opt in options] + [["Назад"]]
    return ReplyKeyboardMarkup(keys, resize_keyboard=True)

def send_long(update: Update, text: str, **kwargs):
    MAX = 4000
    while text:
        part, text = text[:MAX], text[MAX:]
        try:
            update.message.reply_text(part, **kwargs)
        except BadRequest:
            update.message.reply_text(part)

@app.route('/webhook', methods=['POST'])
def webhook():
    upd = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(upd)
    return jsonify({'ok': True})

def start(update: Update, context: CallbackContext):
    uid = update.message.from_user.id
    try:
        bz, rz, names = load_zones()
    except Exception as e:
        return update.message.reply_text(f"Ошибка загрузки прав доступа: {e}")
    if uid not in bz:
        return update.message.reply_text("К сожалению, у вас нет доступа, обратитесь к администратору.")
    branch, res, name = bz[uid], rz[uid], names[uid]

    if branch == "All" and res == "All":
        context.user_data['mode'] = 1
        send_long(update,
            f"Приветствую Вас, {name}! Вы можете осуществлять поиск в любом филиале.\nНажмите «Выбор филиала».",
            reply_markup=kb_only_select()
        )
    elif branch != "All" and res == "All":
        context.user_data['mode'] = 2
        context.user_data['current_branch'] = branch
        send_long(update,
            f"Приветствую Вас, {name}! Вы можете просматривать только филиал {branch}.",
            reply_markup=kb_search_select()
        )
    else:
        context.user_data['mode'] = 3
        context.user_data['current_branch'] = branch
        context.user_data['current_res']    = res
        send_long(update,
            f"Приветствую Вас, {name}! Вы можете просматривать только {res} РЭС филиала {branch}.",
            reply_markup=kb_search_select()
        )
    context.user_data.pop('ambiguous', None)
    context.user_data.pop('ambiguous_df', None)

def handle_text(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    uid  = update.message.from_user.id
    bz, rz, names = load_zones()
    if uid not in bz:
        return update.message.reply_text("К сожалению, у вас нет доступа, обратитесь к администратору.")
    branch, res, name = bz[uid], rz[uid], names[uid]
    mode = context.user_data.get('mode', 1)

    # Разрешаем выйти из неоднозначности
    if context.user_data.get('ambiguous'):
        if text == "Назад":
            context.user_data.pop('ambiguous')
            context.user_data.pop('ambiguous_df')
            return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=kb_search_select())
        if text in context.user_data['ambiguous']:
            found = context.user_data['ambiguous_df']
            tp_sel = text
            lines = [f"На {tp_sel} {len(found)} ВОЛС с договором аренды.", ""]
            for _, r0 in found.iterrows():
                lines.append(f"ВЛ {r0['Уровень напряжения']} {r0['Наименование ВЛ']}:")
                lines.append(f"Опоры: {r0['Опоры']}")
                lines.append(f"Кол-во опор: {r0['Количество опор']}")
                prov = r0.get('Наименование Провайдера', '')
                num  = r0.get('Номер договора', '')
                tail = f", {num}" if num else ""
                lines.append(f"Провайдер: {prov}{tail}")
                lines.append("")
            resp = "\n".join(lines).strip()
            send_long(update, resp, reply_markup=kb_search_select())
            send_long(update, f"{name}, задание выполнено!", reply_markup=kb_search_select())
            context.user_data.pop('ambiguous')
            context.user_data.pop('ambiguous_df')
            return

    # Кнопка выбор филиала
    if text == "Выбор филиала":
        if mode == 1:
            return update.message.reply_text("Выберите филиал:", reply_markup=kb_select_branch())
        elif mode == 2:
            return update.message.reply_text(f"{name}, Вы можете просматривать только филиал {branch}.", reply_markup=kb_search_select())
        else:
            return update.message.reply_text(f"{name}, Вы можете просматривать только {res} РЭС филиала {branch}.", reply_markup=kb_search_select())

    # Определяем branch_search
    if mode == 1:
        if text in BRANCHES:
            context.user_data['current_branch'] = text
            return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=kb_search_select())
        if text != "Поиск по ТП":
            return update.message.reply_text("Нажмите «Поиск по ТП» или «Выбор филиала».", reply_markup=kb_search_select())
        if 'current_branch' not in context.user_data:
            return update.message.reply_text("Сначала выберите филиал:", reply_markup=kb_select_branch())
        branch_search = context.user_data['current_branch']
    else:
        if text == "Поиск по ТП":
            return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=kb_search_select())
        branch_search = branch

    # Загружаем таблицу
    table_url = BRANCH_URLS.get(branch_search)
    try:
        df = pd.read_csv(normalize_sheet_url(table_url))
    except Exception as e:
        return update.message.reply_text(f"Ошибка загрузки таблицы: {e}", reply_markup=kb_search_select())

    # Фильтр по РЭС для mode 3
    if mode == 3:
        df = df[df["РЭС"] == res]

    # Обработка ввода ТП
    tp = text.upper().replace("ТП-", "").strip()
    df['D_UP'] = df['Наименование ТП'].str.upper().str.replace("ТП-", "")
    matched = df[df['D_UP'].str.contains(tp, na=False)]

    if matched.empty:
        msg = "Договоров ВОЛС на данной ТП нет, либо название ТП введено некорректно."
        return update.message.reply_text(msg, reply_markup=kb_search_select())
    if len(matched['Наименование ТП'].unique()) > 1:
        options = matched['Наименование ТП'].unique().tolist()
        context.user_data['ambiguous']    = options
        context.user_data['ambiguous_df'] = matched
        return update.message.reply_text(
            "Возможно вы искали другое ТП, выберите из списка ниже:",
            reply_markup=kb_ambiguous(options)
        )

    # Единичный результат
    tp_name = matched.iloc[0]['Наименование ТП']
    lines = [f"На {tp_name} {len(matched)} ВОЛС с договором аренды.", ""]
    for _, r0 in matched.iterrows():
        lines.append(f"ВЛ {r0['Уровень напряжения']} {r0['Наименование ВЛ']}:")
        lines.append(f"Опоры: {r0['Опоры']}")
        lines.append(f"Кол-во опор: {r0['Количество опор']}")
        prov = r0.get('Наименование Провайдера', '')
        num  = r0.get('Номер договора', '')
        tail = f", {num}" if num else ""
        lines.append(f"Провайдер: {prov}{tail}")
        lines.append("")
    resp = "\n".join(lines).strip()
    send_long(update, resp, reply_markup=kb_search_select())
    send_long(update, f"{name}, задание выполнено!", reply_markup=kb_search_select())

dispatcher.add_handler(CommandHandler('start', start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

def ping_self():
    if not SELF_URL:
        return
    while True:
        try:
            requests.get(f"{SELF_URL}/webhook")
        except:
            pass
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=ping_self, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
