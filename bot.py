import os
import json
import hmac
import hashlib
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, PreCheckoutQueryHandler, ContextTypes, filters

# ─────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8298157683:AAG8-TLkM4hpNZdOocWRqEr7BywKEc3rea0")
GROK_API_KEY   = os.environ.get("GROK_API_KEY",   "xai-RtSTObHe0BctIMk8s5vWzgyWAMG18pqeNW8ONIvbaDuEbBX4HYJpOqNDR8n1SVLIV0HOyn0r1ipDZUPr")
WEB_APP_URL    = os.environ.get("WEB_APP_URL",    "https://steady-brioche-e0b7ee.netlify.app")
DB_PATH        = os.environ.get("DB_PATH",        "users.db")

# Белый список — user_id с безлимитным доступом
WHITELIST = {
    Rival,  # добавь сюда свои Telegram user_id
}

# Тарифы в Stars (1 Star ≈ $0.013)
TARIFF_1    = {"label": "1 проверка",     "stars": 50,  "credits": 1,  "unlimited_days": 0}
TARIFF_5    = {"label": "5 проверок",     "stars": 200, "credits": 5,  "unlimited_days": 0}
TARIFF_UNL  = {"label": "Безлимит / 30 дней", "stars": 500, "credits": 0, "unlimited_days": 30}

FREE_CHECKS = 2   # сколько бесплатных проверок даём новым пользователям

# ─────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────
def db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id         INTEGER PRIMARY KEY,
                free_used       INTEGER DEFAULT 0,
                credits         INTEGER DEFAULT 0,
                unlimited_until TEXT    DEFAULT NULL,
                created_at      TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

def get_user(user_id: int) -> sqlite3.Row:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row

def add_credits(user_id: int, credits: int):
    get_user(user_id)
    with db_conn() as conn:
        conn.execute("UPDATE users SET credits = credits + ? WHERE user_id=?", (credits, user_id))
        conn.commit()

def add_unlimited(user_id: int, days: int):
    get_user(user_id)
    until = (datetime.utcnow() + timedelta(days=days)).isoformat()
    with db_conn() as conn:
        # Если уже есть активный безлимит — продлеваем от текущей даты
        row = conn.execute("SELECT unlimited_until FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row["unlimited_until"]:
            try:
                current = datetime.fromisoformat(row["unlimited_until"])
                if current > datetime.utcnow():
                    until = (current + timedelta(days=days)).isoformat()
            except Exception:
                pass
        conn.execute("UPDATE users SET unlimited_until=? WHERE user_id=?", (until, user_id))
        conn.commit()

def spend_check(user_id: int) -> str:
    """
    Возвращает 'whitelist' | 'free' | 'credits' | 'unlimited' | 'denied'
    """
    if user_id in WHITELIST:
        return "whitelist"
    user = get_user(user_id)
    # Безлимит
    if user["unlimited_until"]:
        try:
            if datetime.fromisoformat(user["unlimited_until"]) > datetime.utcnow():
                return "unlimited"
        except Exception:
            pass
    # Бесплатные
    if user["free_used"] < FREE_CHECKS:
        with db_conn() as conn:
            conn.execute("UPDATE users SET free_used = free_used + 1 WHERE user_id=?", (user_id,))
            conn.commit()
        return "free"
    # Кредиты
    if user["credits"] > 0:
        with db_conn() as conn:
            conn.execute("UPDATE users SET credits = credits - 1 WHERE user_id=?", (user_id,))
            conn.commit()
        return "credits"
    return "denied"

def get_status_text(user_id: int) -> str:
    """Текстовый статус для команды /status"""
    if user_id in WHITELIST:
        return "♾️ Безлимитный доступ (whitelist)"
    user = get_user(user_id)
    lines = []
    # Безлимит
    if user["unlimited_until"]:
        try:
            until = datetime.fromisoformat(user["unlimited_until"])
            if until > datetime.utcnow():
                lines.append(f"♾️ Безлимит до {until.strftime('%d.%m.%Y %H:%M')} UTC")
        except Exception:
            pass
    # Кредиты
    if user["credits"] > 0:
        lines.append(f"🎟 Кредитов: {user['credits']}")
    # Бесплатные
    free_left = max(0, FREE_CHECKS - user["free_used"])
    if free_left > 0:
        lines.append(f"🆓 Бесплатных проверок осталось: {free_left}")
    if not lines:
        lines.append("❌ Нет доступа — купи тариф /buy")
    return "\n".join(lines)

# ─────────────────────────────────────────
#  TELEGRAM INITDATA VERIFICATION
# ─────────────────────────────────────────
def verify_telegram_init_data(init_data: str) -> dict | None:
    """
    Проверяет подпись Telegram WebApp initData.
    Возвращает dict с данными или None если подпись неверна.
    """
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed.items())
        )
        secret_key = hmac.new(
            b"WebAppData", TELEGRAM_TOKEN.encode(), hashlib.sha256
        ).digest()
        expected_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_hash, received_hash):
            return None

        user_data = json.loads(parsed.get("user", "{}"))
        return user_data
    except Exception:
        return None

# ─────────────────────────────────────────
#  FLASK BACKEND (работает в том же процессе)
# ─────────────────────────────────────────
flask_app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

@flask_app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response

@flask_app.route("/check", methods=["OPTIONS", "POST"])
def check_access_endpoint():
    """WebApp вызывает этот эндпоинт перед началом проверки."""
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data      = request.get_json(force=True) or {}
    init_data = data.get("initData", "")

    user_info = verify_telegram_init_data(init_data)
    if not user_info:
        return jsonify({"ok": False, "error": "invalid_auth"}), 403

    user_id = user_info.get("id")
    if not user_id:
        return jsonify({"ok": False, "error": "no_user_id"}), 403

    user = get_user(user_id)
    is_whitelist = user_id in WHITELIST
    free_left    = max(0, FREE_CHECKS - user["free_used"])
    credits      = user["credits"]
    unlimited    = False
    unlimited_until = None
    if user["unlimited_until"]:
        try:
            dt = datetime.fromisoformat(user["unlimited_until"])
            if dt > datetime.utcnow():
                unlimited = True
                unlimited_until = user["unlimited_until"]
        except Exception:
            pass

    has_access = is_whitelist or free_left > 0 or credits > 0 or unlimited

    return jsonify({
        "ok": True,
        "has_access": has_access,
        "is_whitelist": is_whitelist,
        "free_left": free_left,
        "credits": credits,
        "unlimited": unlimited,
        "unlimited_until": unlimited_until,
    })

@flask_app.route("/check_and_proxy", methods=["OPTIONS", "POST"])
def check_and_proxy():
    """
    Проверяет доступ и проксирует запрос к xAI API.
    WebApp больше не вызывает xAI напрямую — только через этот эндпоинт.
    """
    if request.method == "OPTIONS":
        return jsonify({}), 200

    import requests as req_lib

    data      = request.get_json(force=True) or {}
    init_data = data.get("initData", "")
    payload   = data.get("payload", {})  # тело запроса для xAI

    user_info = verify_telegram_init_data(init_data)
    if not user_info:
        return jsonify({"ok": False, "error": "invalid_auth"}), 403

    user_id = user_info.get("id")
    if not user_id:
        return jsonify({"ok": False, "error": "no_user_id"}), 403

    result = spend_check(user_id)
    if result == "denied":
        return jsonify({"ok": False, "error": "payment_required", "access_type": "denied"}), 402

    # Проксируем к xAI
    try:
        xai_resp = req_lib.post(
            "https://api.x.ai/v1/responses",
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {GROK_API_KEY}",
            },
            json=payload,
            timeout=120,
        )
        return jsonify({"ok": True, "access_type": result, "data": xai_resp.json()}), xai_resp.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ─────────────────────────────────────────
#  TELEGRAM BOT HANDLERS
# ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("✍️ Проверить работу", web_app=WebAppInfo(url=WEB_APP_URL))]],
        resize_keyboard=True
    )
    user_id = update.effective_user.id
    get_user(user_id)  # создаём запись если первый раз

    await update.message.reply_text(
        f"👋 Привет! Я помогу проверить твою работу по ЕГЭ.\n\n"
        f"🆓 Для новых пользователей — {FREE_CHECKS} проверки бесплатно.\n\n"
        f"Нажми кнопку ниже 👇\n\n"
        f"📊 Статус: /status\n💳 Купить проверки: /buy",
        reply_markup=keyboard
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        f"📊 Твой баланс:\n{get_status_text(user_id)}"
    )

async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("1 проверка — 50 ⭐", callback_data="buy_1")],
        [InlineKeyboardButton("5 проверок — 200 ⭐ (экономия 20%)", callback_data="buy_5")],
        [InlineKeyboardButton("♾️ Безлимит / 30 дней — 500 ⭐", callback_data="buy_unl")],
    ])
    await update.message.reply_text(
        "💳 Выбери тариф:\n\n"
        "🎟 1 проверка — 50 Stars\n"
        "🎟 5 проверок — 200 Stars (экономия 20%)\n"
        "♾️ Безлимит на 30 дней — 500 Stars\n\n"
        "Оплата через Telegram Stars — безопасно и мгновенно.",
        reply_markup=keyboard
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tariff_map = {
        "buy_1":   TARIFF_1,
        "buy_5":   TARIFF_5,
        "buy_unl": TARIFF_UNL,
    }
    tariff = tariff_map.get(query.data)
    if not tariff:
        return

    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title=tariff["label"],
        description=f"Доступ к проверке работ по ЕГЭ: {tariff['label']}",
        payload=query.data,          # запоминаем что куплено
        currency="XTR",              # Telegram Stars
        prices=[LabeledPrice(tariff["label"], tariff["stars"])],
    )

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram требует ответить в течение 10 секунд."""
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    payload = payment.invoice_payload

    if payload == "buy_1":
        add_credits(user_id, 1)
        text = "✅ Оплата получена! Начислена 1 проверка."
    elif payload == "buy_5":
        add_credits(user_id, 5)
        text = "✅ Оплата получена! Начислено 5 проверок."
    elif payload == "buy_unl":
        add_unlimited(user_id, 30)
        text = "✅ Оплата получена! Безлимит на 30 дней активирован."
    else:
        text = "✅ Оплата получена!"

    await update.message.reply_text(
        f"{text}\n\n📊 Баланс: {get_status_text(user_id)}"
    )

# ─────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────
if __name__ == "__main__":
    init_db()

    # Flask запускаем в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logging.info(f"Flask backend запущен на порту {os.environ.get('PORT', 8080)}")

    from telegram.ext import CallbackQueryHandler

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("buy",    buy_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    logging.info("Telegram bot запущен")
    app.run_polling()
