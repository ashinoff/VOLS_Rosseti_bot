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

app        = Flask(__name__)
bot        = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

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
    df  = pd.read_csv(StringIO(r.content.decode('utf-8-sig')), header=None, skiprows=1)
    bz, rz, names = {}, {}, {}
    for _, row in df.iterrows():
        try:
            uid = int(row[2])
        except:
            continue
        bz[uid]    = row[0].strip()  # Филиал или All
        rz[uid]    = row[1].strip()  # РЭС или All
        names[uid] = row[3].strip()  # ФИО
    return bz, rz, names

def main_kb(is_all):
    return ReplyKeyboardMarkup([["Выбор филиала"]] if is_all else [["Поиск"]], resize_keyboard=True)

def branch_kb():
    return ReplyKeyboardMarkup([[b] for b in BRANCHES], resize_keyboard=True)

def action_kb():
    return ReplyKeyboardMarkup([["Поиск ТП"], ["Выбор филиала"]], resize_keyboard=True)

@app.route('/webhook', methods=['POST'])
def webhook():
    upd = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(upd)
    return jsonify({'ok':True})

def start(update: Update, context: CallbackContext):
    uid = update.message.from_user.id
    try:
        bz, rz, names = load_zones()
    except Exception as e:
        return update.message.reply_text(f"Ошибка загрузки прав доступа: {e}")
    if uid not in bz:
        return update.message.reply_text("К сожалению, у вас нет доступа, обратитесь к администратору.")
    branch = bz[uid]; res = rz[uid]; name = names[uid]
    if branch=="All":
        text = f"Приветствую Вас, {name}! Вы можете просматривать только филиал ЭС."
        # для ALL: кнопка только Выбор филиала
        update.message.reply_text(text+"\nНажмите «Выбор филиала».", reply_markup=main_kb(True))
    else:
        text = f"Приветствую Вас, {name}! Вы можете просматривать только филиал {branch}."
        update.message.reply_text(text+"\nНажмите «Поиск».", reply_markup=main_kb(False))
    context.user_data.clear()
    context.user_data['branch']=None

def handle_text(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    uid = update.message.from_user.id
    bz, rz, names = load_zones()
    if uid not in bz:
        return update.message.reply_text("К сожалению, у вас нет доступа, обратитесь к администратору.")
    branch = bz[uid]; res=rz[uid]; name=names[uid]

    # Выбор филиала
    if text=="Выбор филиала":
        if branch=="All":
            return update.message.reply_text("Выберите филиал:", reply_markup=branch_kb())
        else:
            return update.message.reply_text(
                f"Приветствую Вас, {name}! Вы можете просматривать только филиал {branch}.",
                reply_markup=action_kb()
            )

    # Поиск / Поиск ТП
    if text in ("Поиск","Поиск ТП"):
        if branch=="All":
            return update.message.reply_text("Выберите филиал:", reply_markup=branch_kb())
        context.user_data['branch']=branch
        return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=action_kb())

    # Выбор филиала из списка
    if text in BRANCHES:
        context.user_data['branch']=text
        return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=action_kb())

    # Обработка ТП
    if context.user_data.get('branch'):
        br = context.user_data['branch']
        url=BRANCH_URLS[br]
        try:
            df=pd.read_csv(normalize_sheet_url(url))
        except Exception as e:
            return update.message.reply_text(f"Ошибка загрузки таблицы: {e}", reply_markup=action_kb())
        # зона3: конкретный РЭС
        if branch!="All" and res!="All":
            df=df[df["РЭС"]==res]
        tp=text.upper().replace("ТП-","").strip()
        df['D']=df['Наименование ТП'].str.upper().str.replace("ТП-","")
        found=df[df['D'].str.contains(tp,na=False)]
        if found.empty:
            resp="Договоров ВОЛС на данной ТП нет, либо название ТП введено некорректно."
        else:
            lines=[f"Найдено {len(found)} ВОЛС с договором аренды:",
                   f"{found.iloc[0]['Наименование ТП']} находится в {found.iloc[0]['РЭС']}"]
            for _,r in found.iterrows():
                lines.append("")  # раздел
                lines.append(f"ВЛ {r['Наименование ВЛ']}:")
                lines.append(f"Опоры: {r['Опоры']}")
                lines.append(f"Кол-во опор: {r['Количество опор']}")
                lines.append(f"Провайдер: {r['Наименование Провайдера']}")
            resp="\n".join(lines)
        update.message.reply_text(resp)
        return update.message.reply_text(f"{name}, задание выполнено.", reply_markup=action_kb())

    # Иначе
    return update.message.reply_text("Нажмите одну из кнопок меню.", reply_markup=main_kb(branch=="All"))

dispatcher.add_handler(CommandHandler('start', start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

def ping():
    if not SELF_URL: return
    while True:
        try: requests.get(f"{SELF_URL}/webhook")
        except: pass
        time.sleep(300)

if __name__=='__main__':
    threading.Thread(target=ping,daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT',5000)))
