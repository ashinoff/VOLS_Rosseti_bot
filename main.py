import os
import threading
import time
import re
import requests
import csv
from io import StringIO
from flask import Flask, request, jsonify
from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext

# === ENVIRONMENT VARIABLES ===
TOKEN           = os.getenv("TOKEN")
SELF_URL        = os.getenv("SELF_URL", "").rstrip('/')
ZONES_CSV_URL   = os.getenv("ZONES_CSV_URL", "").strip()

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
    if "/export" in url or url.endswith(".csv"):
        return url
    m = re.search(r"/d/e/([\w-]+)/", url)
    if m:
        sid = m.group(1)
        return f"https://docs.google.com/spreadsheets/d/e/{sid}/export?format=csv&gid=0"
    m = re.search(r"/d/([\w-]+)", url)
    if m:
        sid = m.group(1)
        return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid=0"
    m = re.search(r"/file/d/([\w-]+)", url)
    if m:
        fid = m.group(1)
        return f"https://drive.google.com/uc?export=download&id={fid}"
    return url

def load_zones() -> dict:
    """
    Скачиваем CSV, парсим его через csv.DictReader
    и возвращаем словарь {user_id: filial}.
    """
    url = normalize_sheet_url(ZONES_CSV_URL)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()

    text = resp.text
    # иногда в начале файла бывает BOM — обрежем:
    if text.startswith("\ufeff"):
        text = text.encode("utf-8").decode("utf-8-sig")

    reader = csv.DictReader(StringIO(text))
    # Стандартизируем имена колонок:
    headers = [h.strip().upper() for h in reader.fieldnames]
    reader.fieldnames = headers

    required = {"ФИЛИАЛ", "РЭС", "ID", "ФИО"}
    if not required.issubset(headers):
        missing = required - set(headers)
        raise ValueError(f"В файле зон не все колонки присутствуют: {missing}")

    zones = {}
    for row in reader:
        try:
            uid = int(row["ID"].strip())
            filial = row["ФИЛИАЛ"].strip()
            zones[uid] = filial
        except Exception:
            continue
    return zones

# === KEYBOARDS ===

def main_menu_keyboard():
    return ReplyKeyboardMarkup([["Поиск"]], resize_keyboard=True)

def branches_keyboard():
    # Однокнопочная колонка + кнопка «Назад»
    keys = [[b] for b in BRANCHES]
    keys.append(["Назад"])
    return ReplyKeyboardMarkup(keys, resize_keyboard=True)

# === HANDLERS ===

def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Добро пожаловать! Нажмите «Поиск», чтобы выбрать филиал.",
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
    # приводим к lower для «all», если надо:
    if filial and filial.lower() == "all":
        update.message.reply_text("Выберите филиал:", reply_markup=branches_keyboard())
    elif filial in BRANCHES:
        update.message.reply_text(f"Поиск будет выполняться для филиала «{filial}».")
    else:
        update.message.reply_text("У вас нет прав доступа.")

# === SELF-PING ===

def ping_self():
    if not SELF_URL:
        return
    while True:
        try:
            requests.get(SELF_URL + "/webhook")
        except:
            pass
        time.sleep(300)

# === REGISTRATION ===

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(Filters.regex("^Поиск$"), handle_search))

# === WEBHOOK ===

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return jsonify({"status": "ok"})

# === ENTRYPOINT ===

if __name__ == "__main__":
    threading.Thread(target=ping_self, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
