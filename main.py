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

    # --- возврат из неоднозначности ---
    if context.user_data.get('ambiguous'):
        if text == "Назад":
            context.user_data.pop('ambiguous')
            context.user_data.pop('ambiguous_df')
            return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=kb_search_select())

        options = context.user_data['ambiguous']
        if text in options:
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
            update.message.reply_text(resp, reply_markup=kb_search_select())
            update.message.reply_text(f"{name}, задание выполнено!", reply_markup=kb_search_select())
            context.user_data.pop('ambiguous')
            context.user_data.pop('ambiguous_df')
            return

    # кнопка «Выбор филиала»
    if text == "Выбор филиала":
        if mode == 1:
            return update.message.reply_text("Выберите филиал:", reply_markup=kb_select_branch())
        elif mode == 2:
            return update.message.reply_text(f"{name}, Вы можете просматривать только филиал {branch}.", reply_markup=kb_search_select())
        else:
            return update.message.reply_text(f"{name}, Вы можете просматривать только {res} РЭС филиала {branch}.", reply_markup=kb_search_select())

    # определяем branch_search для поиска
    if mode == 1:
        if text in BRANCHES:
            context.user_data['current_branch'] = text
            return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=kb_search_select())
        if text == "Поиск по ТП":
            if 'current_branch' not in context.user_data:
                return update.message.reply_text("Сначала выберите филиал:", reply_markup=kb_select_branch())
            return update.message.reply_text(f"{name}, введите номер ТП.", reply_markup=kb_search_select())
        if 'current_branch' in context.user_data:
            branch_search = context.user_data['current_branch']
        else:
            return

    elif mode == 2:
        branch_search = branch if text in ("Поиск по ТП",) or True else None  # всегда branch

    else:  # mode == 3
        branch_search = branch

    # загрузка таблицы
    try:
        df = pd.read_csv(normalize_sheet_url(BRANCH_URLS[branch_search]))
    except Exception as e:
        return update.message.reply_text(f"Ошибка загрузки таблицы: {e}", reply_markup=kb_search_select())

    # фильтрация по РЭС для режима 3
    if mode == 3:
        df = df[df["РЭС"] == res]

    # подготовка колонок
    df.columns = df.columns.str.strip()

    # нормализация для поиска
    def norm(s): return re.sub(r'\W','', str(s).upper())
    tp_input = norm(text)
    df['D_UP'] = df['Наименование ТП'].apply(norm)

    found = df[df['D_UP'].str.contains(tp_input, na=False)]

    # неоднозначность
    unique_tp = found['Наименование ТП'].unique().tolist()
    if len(unique_tp) > 1:
        kb = ReplyKeyboardMarkup([[tp] for tp in unique_tp] + [["Назад"]], resize_keyboard=True)
        context.user_data['ambiguous']    = unique_tp
        context.user_data['ambiguous_df'] = found
        return update.message.reply_text(
            "Возможно, вы искали другое ТП, выберите из списка ниже:",
            reply_markup=kb
        )

    # ничего не нашлось
    if found.empty:
        return update.message.reply_text(
            "Договоров ВОЛС на данной ТП нет, либо название ТП введено некорректно.",
            reply_markup=kb_search_select()
        )

    # вывод результата
    tp_name = unique_tp[0]
    lines = [f"На {tp_name} {len(found)} ВОЛС с договором аренды.", ""]
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

    update.message.reply_text(resp, reply_markup=kb_search_select())
    update.message.reply_text(f"{name}, задание выполнено!", reply_markup=kb_search_select())

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
