import os
import threading
import pandas as pd
import requests
from io import StringIO
from flask import Flask, request
from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext

app = Flask(__name__)

# === ENV ===
TOKEN              = os.getenv("TOKEN")
SELF_URL           = os.getenv("SELF_URL")
ZONES_CSV_URL      = os.getenv("ZONES_CSV_URL")
BRANCH_SHEETS_MAP  = {
    k.strip(): v.strip()
    for k,v in (p.split("=",1) for p in os.getenv("BRANCH_SHEETS_MAP","").split(",") if "=" in p)
}

bot        = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)
user_states = {}   # user_id -> state dict

# Загрузка зон доступа
def load_zones():
    r = requests.get(ZONES_CSV_URL, timeout=10); r.raise_for_status()
    df = pd.read_csv(StringIO(r.content.decode("utf-8-sig")), dtype=str)
    zones = {}
    for _, row in df.iterrows():
        uid = str(row["ID"]).strip()
        zones[uid] = {
            "filial": row["Филиал"].strip(),
            "res":    row["РЭС"].strip(),
            "name":   row.get("ФИО","").strip()
        }
    return zones

# Клавиатуры
def keyboard_rows(labels):
    return ReplyKeyboardMarkup([[l] for l in labels], resize_keyboard=True)

def start_menu(user_zone):
    if user_zone["filial"] == "All":
        # список филиалов + Назад
        keys = list(BRANCH_SHEETS_MAP.keys()) + ["Назад"]
        return "Выберите филиал:", keyboard_rows(keys)
    else:
        # конкретный филиал: Поиск ТП и Справка
        return "Главное меню:", keyboard_rows(["Поиск ТП","СПРАВКА"])

def tp_input_menu(branch_name):
    return f"Введите номер ТП для филиала «{branch_name}» (например: К1):", keyboard_rows(["Назад"])

HELP_MENU = keyboard_rows([
    "Сечение кабеля",
    "Справка формулы",
    "Назад"
])

# Handler /start
def start(update: Update, context: CallbackContext):
    uid = str(update.effective_user.id)
    zones = load_zones()
    if uid not in zones:
        return update.message.reply_text("У вас нет прав доступа.")
    user_states[uid] = {}  # сброс
    text, kb = start_menu(zones[uid])
    update.message.reply_text(text, reply_markup=kb)

# Основной месседж-хендлер
def handle_message(update: Update, context: CallbackContext):
    uid  = str(update.effective_user.id)
    txt  = update.message.text.strip()
    zones = load_zones()
    if uid not in zones:
        return update.message.reply_text("У вас нет прав доступа.")
    zone = zones[uid]
    st   = user_states.get(uid, {})

    # Кнопка Назад в любой момент
    if txt == "Назад":
        user_states[uid] = {}
        text, kb = start_menu(zone)
        return update.message.reply_text(text, reply_markup=kb)

    # Шаг 1: филиал выбирает только All-пользователь
    if zone["filial"] == "All" and not st.get("step"):
        if txt in BRANCH_SHEETS_MAP:
            # запомним выбор филиала
            user_states[uid] = {"step":"branch_selected", "branch":txt}
            text, kb = tp_input_menu(txt)
            return update.message.reply_text(text, reply_markup=kb)
        else:
            return update.message.reply_text("Пожалуйста, выберите филиал из списка.", reply_markup=keyboard_rows(list(BRANCH_SHEETS_MAP.keys())+["Назад"]))

    # Шаг 2: ввод ТП
    if (zone["filial"]!="All" or st.get("step")=="branch_selected") and not st.get("step")=="tp_entered":
        # начало поиска ТП: если конкретный филиал — проверяем кнопку
        if zone["filial"]!="All" and txt=="Поиск ТП":
            user_states[uid] = {"step":"tp_prompt", "branch":zone["filial"]}
            text, kb = tp_input_menu(zone["filial"])
            return update.message.reply_text(text, reply_markup=kb)
        if zone["filial"]!="All" and txt=="СПРАВКА":
            # вставьте здесь вашу справку
            return update.message.reply_text("Справочная информация...", reply_markup=HELP_MENU)

        # если мы спросили ТП
        if st.get("step") in ("branch_selected","tp_prompt"):
            branch = st["branch"]
            tp_id  = txt
            # подгружаем excel
            url = BRANCH_SHEETS_MAP.get(branch)
            try:
                df = pd.read_excel(url, dtype=str)
            except Exception as e:
                return update.message.reply_text(f"Ошибка загрузки данных: {e}")
            # фильтруем по РЭС
            if zone["res"] != "All":
                df = df[df["РЭС"].str.strip()==zone["res"]]
            # ищем введённый ТП
            row = df[df["Наименование ТП"].str.strip().eq(tp_id)]
            if row.empty:
                return update.message.reply_text("ТП не найдено. Попробуйте ещё.", reply_markup=tp_input_menu(branch))
            # берём первую строку
            r = row.iloc[0]
            # формируем сообщение
            out = (
                f"ТП: {r['Наименование ТП']}\n"
                f"Ур. напр.: {r['Уровень напряжения']}\n"
                f"ВЛ: {r['Наименование ВЛ']}\n"
                f"ВУ: {r.get('ВУ (если необходимо)','')}\n"
                f"Опоры: {r['Опоры']}\n"
                f"Кол-во опор: {r['Количество опор']}\n"
                f"Провайдер: {r['Наименование Провайдера']}"
            )
            user_states[uid] = {}  # сброс до начала
            text, kb = start_menu(zone)
            update.message.reply_text(out)
            return update.message.reply_text(text, reply_markup=kb)

    # fallback
    text, kb = start_menu(zone)
    update.message.reply_text(text, reply_markup=kb)

# Webhook
@app.route("/webhook", methods=["POST"])
def webhook():
    dispatcher.process_update(Update.de_json(request.get_json(force=True), bot))
    return "ok"

@app.route("/")
def index():
    return "OK"

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

if __name__=="__main__":
    # keep-alive ping
    def ping():
        try: requests.get(SELF_URL, timeout=5)
        except: pass
        t = threading.Timer(9*60, ping); t.daemon=True; t.start()
    ping()
    bot.set_webhook(f"{SELF_URL}/webhook")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
