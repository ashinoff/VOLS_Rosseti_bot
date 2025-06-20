import os
import re
from io import StringIO

import pandas as pd
import requests
from flask import Flask, request
from telegram import Bot, Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext

app = Flask(__name__)

# === ENV ===
TOKEN         = os.getenv("TOKEN")
SELF_URL      = os.getenv("SELF_URL")
# Указываем URL на CSV-файл зон доступа
ZONES_CSV_URL = os.getenv("ZONES_CSV_URL", "").strip()
# Формат переменной: "Филиал1=GID1, Филиал2=GID2, …"
BRANCH_SHEETS_MAP = {
    k.strip(): v.strip()
    for k, v in (p.split("=", 1) for p in os.getenv("BRANCH_SHEETS_MAP", "").split(",") if "=" in p)
}

bot        = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)
user_states = {}   # карта user_id → текущее состояние

# --- Утилиты ---

def normalize_sheet_url(url: str, default_gid: int = 0) -> str:
    """
    Преобразует ссылку Google Sheets или Google Drive на CSV-документ
    в прямую ссылку для скачивания CSV.
    """
    url = url.strip()
    # если уже корректный экспорт или прямой .csv
    if '/export' in url or url.endswith('.csv'):
        return url
    # Google Sheets URL вида /d/{ID}/
    m = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    if m:
        sheet_id = m.group(1)
        return f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={default_gid}'
    # Google Drive file URL вида /file/d/{ID}/view
    m2 = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if m2:
        file_id = m2.group(1)
        return f'https://drive.google.com/uc?export=download&id={file_id}'
    return url

def load_zones() -> dict:
    """
    Загружает CSV-файл зон доступа и возвращает словарь:
    { user_id (str) → {filial, res, name} }
    """
    url = normalize_sheet_url(ZONES_CSV_URL)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()

    df = pd.read_csv(StringIO(resp.text), dtype=str)
    # нормализуем заголовки в верхний регистр + убираем пробелы
    df.columns = df.columns.str.strip().str.upper()
    required = {"ФИЛИАЛ", "РЭС", "ID", "ФИО"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"В файле зон не все колонки присутствуют: {missing!r}")

    zones = {}
    for _, row in df.iterrows():
        uid = str(row["ID"]).strip()
        zones[uid] = {
            "filial": row["ФИЛИАЛ"].strip(),
            "res":    row["РЭС"].strip(),
            "name":   row["ФИО"].strip(),
        }
    return zones

# --- Клавиатуры ---

def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [["Поиск"], ["Справочная информация"], ["Уведомление всем"]],
        resize_keyboard=True
    )

# --- Обработчики команд ---

def start(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    name = load_zones().get(user_id, {}).get("name", "пользователь")
    update.message.reply_text(
        f"Здравствуйте, {name}!",
        reply_markup=main_menu_keyboard()
    )

# Здесь должны идти остальные хендлеры:
# - Поиск: выбор филиала, ввод ТП, показ результата
# - Справочная информация
# - Уведомление всем
# и т.д.

# --- Запуск веб-сервиса ---

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

if __name__ == "__main__":
    # Настройка dispatcher'а
    dispatcher.add_handler(CommandHandler("start", start))
    # ... добавляем остальные handlers здесь ...
    # Запускаем Flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
