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
        bz[uid]    = row[0].strip()  # филиал или All
        rz[uid]    = row[1].strip()  # РЭС или All
        names[uid] = row[3].strip()  # ФИО
    return bz, rz, names

# клавиатуры
def kb_select_branch():
    return ReplyKeyboardMarkup([[b] for b in BRANCHES], resize_keyboard=True)

def kb_search_select():
    return ReplyKeyboardMarkup([["Поиск по ТП"], ["Выбор филиала"]], resize_keyboard=True)

def kb_only_select():
    return ReplyKeyboardMarkup([["Выбор филиала"]], resize_keyboard=True)

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
    # определить зону
    if branch == "All" and res == "All":
        context.user_data['mode'] = 1
        update.message.reply_text(
            f"Приветствую Вас, {name}! Вы можете осуществлять поиск в любом филиале.\n"
            f"Нажмите «Выбор филиала».",
            reply_markup=kb_only_select()
        )
    elif branch != "All" and res == "All":
        context.user_data['mode'] = 2
        context.user_data['current_branch'] = branch
        update.message.reply_text(
            f"Приветствую Вас, {name}! Вы можете просматривать только филиал {branch}.",
            reply_markup=kb_search_select()
        )
    else:
        context.user_data['mode'] = 3
        context.user_data['current_branch'] = branch
        context.user_data['current_res']    = res
        update.message.reply_text(
            f"Приветствую Вас, {name}! Вы можете просматривать только {res} РЭС филиала {branch}.",
            reply_markup=kb_search_select()
        )
    context.user_data.pop('await_search', None)

def handle_text(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    uid  = update.message.from_user.id
    bz, rz, names = load_zones()
    if uid not in bz:
        return update.message.reply_text("К сожалению, у вас нет доступа, обратитесь к администратору.")
    branch, res, name = bz[uid], rz[uid], names[uid]
    mode = context.user_data.get('mode', 1)

    # кнопка "Выбор филиала"
    if text == "Выбор филиала":
        if mode == 1:
            return update.message.reply_text("Выберите филиал:", reply_markup=kb_select_branch())
        elif mode == 2:
            return update.message.reply_text(f"{name}, Вы можете просматривать только филиал {branch}.", reply_markup=kb_search_select())
        else:
            return update.message.reply_text(f"{name}, Вы можете просматривать только {res} РЭС филиала {branch}.", reply_markup=kb_search_select())

    # зона1: All/All
    if mode == 1:
        # выбор филиала из списка
        if text in BRANCHES:
            context.user_data['current_branch'] = text
            return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=kb_search_select())
        # кнопка поиска
        if text == "Поиск по ТП":
            if 'current_branch' not in context.user_data:
                return update.message.reply_text("Сначала выберите филиал:", reply_markup=kb_select_branch())
            return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=kb_search_select())
        # ввод ТП
        if 'current_branch' in context.user_data:
            branch_search = context.user_data['current_branch']
            table_url = BRANCH_URLS[branch_search]
            try:
                df = pd.read_csv(normalize_sheet_url(table_url))
            except Exception as e:
                return update.message.reply_text(f"Ошибка загрузки таблицы: {e}", reply_markup=kb_search_select())
            tp = text.upper().replace("ТП-", "").strip()
            df['D'] = df['Наименование ТП'].str.upper().str.replace("ТП-", "")
            found = df[df['D'].str.contains(tp, na=False)]
            if found.empty:
                return update.message.reply_text(
                    "Договоров ВОЛС на данной ТП нет, либо название ТП введено некорректно.",
                    reply_markup=kb_search_select()
                )
            lines = [
                f"Найдено {len(found)} ВОЛС с договором аренды.",
                f"{name}, задание выполнено!"
            ]
            for _, r in found.iterrows():
                lines.append("")
                lines.append(f"ВЛ {r['Наименование ВЛ']}:")
                lines.append(f"Опоры: {r['Опоры']}")
                lines.append(f"Кол-во опор: {r['Количество опор']}")
                lines.append(f"Провайдер: {r['Наименование Провайдера']}")
            return update.message.reply_text("\n".join(lines), reply_markup=kb_search_select())

    # зона2: branch/All
    if mode == 2:
        if text == "Поиск по ТП":
            return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=kb_search_select())
        # ввод ТП
        if text not in ("Поиск по ТП",):
            table_url = BRANCH_URLS[branch]
            try:
                df = pd.read_csv(normalize_sheet_url(table_url))
            except Exception as e:
                return update.message.reply_text(f"Ошибка загрузки таблицы: {e}", reply_markup=kb_search_select())
            tp = text.upper().replace("ТП-", "").strip()
            df['D'] = df['Наименование ТП'].str.upper().str.replace("ТП-", "")
            found = df[df['D'].str.contains(tp, na=False)]
            if found.empty:
                return update.message.reply_text(
                    "Договоров ВОЛС на данной ТП нет, либо название ТП введено некорректно.",
                    reply_markup=kb_search_select()
                )
            lines = [
                f"Найдено {len(found)} ВОЛС с договором аренды.",
                f"{name}, задание выполнено!"
            ]
            for _, r in found.iterrows():
                lines.append("")
                lines.append(f"ВЛ {r['Наименование ВЛ']}:")
                lines.append(f"Опоры: {r['Опоры']}")
                lines.append(f"Кол-во опор: {r['Количество опор']}")
                lines.append(f"Провайдер: {r['Наименование Провайдера']}")
            return update.message.reply_text("\n".join(lines), reply_markup=kb_search_select())

    # зона3: branch/res
    if mode == 3:
        if text == "Поиск по ТП":
            return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=kb_search_select())
        # ввод ТП
        if text not in ("Поиск по ТП",):
            table_url = BRANCH_URLS[branch]
            try:
                df_full = pd.read_csv(normalize_sheet_url(table_url))
            except Exception as e:
                return update.message.reply_text(f"Ошибка загрузки таблицы: {e}", reply_markup=kb_search_select())
            # проверяем, возможно ТП вообще есть в другом РЭС
            all_tp_list = df_full['Наименование ТП'].str.upper().str.replace("ТП-", "").tolist()
            df = df_full[df_full["РЭС"] == res]
            tp = text.upper().replace("ТП-", "").strip()
            df['D'] = df['Наименование ТП'].str.upper().str.replace("ТП-", "")
            found = df[df['D'].str.contains(tp, na=False)]
            if found.empty:
                if tp in all_tp_list:
                    return update.message.reply_text(
                        f"Задание на поиск относится к {res} РЭС, к сожалению у вас нет прав для просмотра.",
                        reply_markup=kb_search_select()
                    )
                return update.message.reply_text(
                    "Договоров ВОЛС на данной ТП нет, либо название ТП введено некорректно.",
                    reply_markup=kb_search_select()
                )
            lines = [
                f"Найдено {len(found)} ВОЛС с договором аренды.",
                f"{name}, задание выполнено!"
            ]
            for _, r in found.iterrows():
                lines.append("")
                lines.append(f"ВЛ {r['Наименование ВЛ']}:")
                lines.append(f"Опоры: {r['Опоры']}")
                lines.append(f"Кол-во опор: {r['Количество опор']}")
                lines.append(f"Провайдер: {r['Наименование Провайдера']}")
            return update.message.reply_text("\n".join(lines), reply_markup=kb_search_select())

    # во всех остальных случаях
    return update.message.reply_text(
        "Нажмите одну из кнопок меню.",
        reply_markup=kb_only_select() if mode == 1 else kb_search_select()
    )

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
