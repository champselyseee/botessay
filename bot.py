from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, WebAppInfo, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters, CallbackQueryHandler
import sqlite3, secrets, time, asyncio
from aiohttp import web

TELEGRAM_TOKEN = "8298157683:AAF2NkNXauFXNqV5RN38TTSoWlwbieu9j2Y"
WEB_APP_URL = "https://steady-brioche-e0b7ee.netlify.app/"
STARS_1 = 25
STARS_5 = 100
STARS_MONTH = 220

# ── Белый список (username без @) ──
WHITELIST = {
    "Champselyseee",
}

# ── База данных ──
def init_db():
    con = sqlite3.connect("users.db")
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            free_used INTEGER DEFAULT 0,
            paid_checks INTEGER DEFAULT 0,
            subscription_until INTEGER DEFAULT 0
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER,
            created_at INTEGER,
            used INTEGER DEFAULT 0
        )
    """)
    con.commit()
    con.close()

def get_user(user_id: int, username: str = None):
    con = sqlite3.connect("users.db")
    row = con.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        con.execute("INSERT INTO users VALUES (?, ?, 0, 0, 0)", (user_id, username))
        con.commit()
        row = con.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    con.close()
    return {"user_id": row[0], "username": row[1], "free_used": row[2], "paid_checks": row[3], "subscription_until": row[4]}

def use_free_check(user_id: int):
    con = sqlite3.connect("users.db")
    con.execute("UPDATE users SET free_used = 1 WHERE user_id = ?", (user_id,))
    con.commit()
    con.close()

def add_paid_checks(user_id: int, count: int):
    con = sqlite3.connect("users.db")
    con.execute("UPDATE users SET paid_checks = paid_checks + ? WHERE user_id = ?", (count, user_id))
    con.commit()
    con.close()

def use_paid_check(user_id: int):
    con = sqlite3.connect("users.db")
    con.execute("UPDATE users SET paid_checks = paid_checks - 1 WHERE user_id = ?", (user_id,))
    con.commit()
    con.close()

def add_subscription(user_id: int, days: int = 30):
    con = sqlite3.connect("users.db")
    now = int(time.time())
    row = con.execute("SELECT subscription_until FROM users WHERE user_id = ?", (user_id,)).fetchone()
    current = row[0] if row and row[0] > now else now
    new_until = current + days * 86400
    con.execute("UPDATE users SET subscription_until = ? WHERE user_id = ?", (new_until, user_id))
    con.commit()
    con.close()
    return new_until

def create_token(user_id: int) -> str:
    token = secrets.token_hex(16)
    con = sqlite3.connect("users.db")
    con.execute("INSERT INTO tokens VALUES (?, ?, ?, 0)", (token, user_id, int(time.time())))
    con.commit()
    con.close()
    return token

def validate_token(token: str) -> bool:
    """Проверяет токен и помечает использованным"""
    con = sqlite3.connect("users.db")
    row = con.execute("SELECT used, created_at FROM tokens WHERE token = ?", (token,)).fetchone()
    if not row:
        con.close()
        return False
    used, created_at = row
    if used or (int(time.time()) - created_at > 1800):
        con.close()
        return False
    con.execute("UPDATE tokens SET used = 1 WHERE token = ?", (token,))
    con.commit()
    con.close()
    return True

def is_whitelisted(username: str) -> bool:
    if not username:
        return False
    return username.lower() in {w.lower() for w in WHITELIST}

def has_subscription(data: dict) -> bool:
    return data["subscription_until"] > int(time.time())

def has_access(data: dict) -> bool:
    return has_subscription(data) or data["paid_checks"] > 0

def webapp_keyboard(token: str) -> ReplyKeyboardMarkup:
    url = f"{WEB_APP_URL}?token={token}"
    return ReplyKeyboardMarkup(
        [[KeyboardButton("✍️ Открыть проверку", web_app=WebAppInfo(url=url))]],
        resize_keyboard=True,
        one_time_keyboard=False
    )

def payment_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💫 1 проверка — {STARS_1} Stars", callback_data="buy_stars_1")],
        [InlineKeyboardButton(f"💫 5 проверок — {STARS_5} Stars", callback_data="buy_stars_5")],
        [InlineKeyboardButton(f"💫 Месяц безлимит — {STARS_MONTH} Stars", callback_data="buy_stars_month")],
        [InlineKeyboardButton("💳 Оплата картой (скоро)", callback_data="buy_card")],
    ])

async def give_access(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict, is_whitelist: bool = False):
    """Выдаёт токен и кнопку. Для подписки/вайтлиста токен не тратит проверки."""
    user_id = data["user_id"]

    if is_whitelist or has_subscription(data):
        token = create_token(user_id)
        sub_text = ""
        if has_subscription(data):
            days_left = (data["subscription_until"] - int(time.time())) // 86400
            sub_text = f"📅 Подписка активна ещё {days_left} дн.\n\n"
        await update.message.reply_text(
            f"{sub_text}Нажми кнопку ниже 👇",
            reply_markup=webapp_keyboard(token)
        )
        return

    # Разовые проверки — токен расходует одну
    token = create_token(user_id)
    use_paid_check(user_id)
    remaining = data["paid_checks"] - 1
    await update.message.reply_text(
        f"✅ Осталось проверок после этой: {remaining}\n\nНажми кнопку 👇",
        reply_markup=webapp_keyboard(token)
    )
    # Если баланс кончился — через 31 мин убираем кнопку
    if remaining == 0:
        asyncio.create_task(remove_keyboard_later(context, user_id))

async def remove_keyboard_later(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    await asyncio.sleep(1860)
    await context.bot.send_message(
        chat_id=chat_id,
        text="⏰ Проверки закончились. Купи ещё → /buy",
        reply_markup=ReplyKeyboardRemove()
    )

# ── /start ──
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username = user.username or ""
    data = get_user(user.id, username)

    if is_whitelisted(username):
        await give_access(update, context, data, is_whitelist=True)
        return

    # Бесплатная проверка
    if not data["free_used"]:
        token = create_token(user.id)
        use_free_check(user.id)
        await update.message.reply_text(
            "👋 Привет! Тебе доступна 1 бесплатная проверка.\n\nНажми кнопку ниже 👇",
            reply_markup=webapp_keyboard(token)
        )
        asyncio.create_task(remove_keyboard_later(context, user.id))
        return

    # Есть подписка или платные проверки
    if has_access(data):
        await give_access(update, context, data)
        return

    # Нет доступа
    await update.message.reply_text(
        "🔒 Доступ закончился.\n\nВыбери способ оплаты:",
        reply_markup=payment_menu()
    )

# ── /buy ──
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Выбери способ оплаты:",
        reply_markup=payment_menu()
    )

# ── /balance ──
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user(user.id, user.username or "")
    if is_whitelisted(user.username or ""):
        await update.message.reply_text("👑 У тебя безлимитный доступ.")
        return
    if has_subscription(data):
        days_left = (data["subscription_until"] - int(time.time())) // 86400
        await update.message.reply_text(f"📅 Подписка активна ещё {days_left} дн.")
        return
    await update.message.reply_text(
        f"📊 Проверок осталось: {data['paid_checks']}\n\nКупить ещё → /buy"
    )

# ── Inline кнопки ──
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    invoices = {
        "buy_stars_1": ("1 проверка ЕГЭ", "Одна проверка по критериям ЕГЭ 2026", "stars_1", STARS_1),
        "buy_stars_5": ("5 проверок ЕГЭ", "Пять проверок по критериям ЕГЭ 2026", "stars_5", STARS_5),
        "buy_stars_month": ("Месяц безлимит", "Безлимитные проверки на 30 дней", "stars_month", STARS_MONTH),
    }

    if query.data in invoices:
        title, desc, payload, price = invoices[query.data]
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=title,
            description=desc,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(title, price)],
        )
    elif query.data == "buy_card":
        await query.message.reply_text(
            "💳 Оплата картой появится совсем скоро!\n"
            "Пока можно оплатить через Telegram Stars 💫"
        )

# ── Оплата Stars ──
async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload
    data = get_user(user_id)

    if payload == "stars_month":
        until = add_subscription(user_id, 30)
        from datetime import datetime
        until_str = datetime.fromtimestamp(until).strftime("%d.%m.%Y")
        token = create_token(user_id)
        await update.message.reply_text(
            f"✅ Подписка активна до {until_str}!\n\nНажми кнопку 👇",
            reply_markup=webapp_keyboard(token)
        )
    else:
        count = 5 if payload == "stars_5" else 1
        add_paid_checks(user_id, count)
        data = get_user(user_id)
        token = create_token(user_id)
        use_paid_check(user_id)
        remaining = data["paid_checks"] - 1
        await update.message.reply_text(
            f"✅ Оплата прошла! Куплено: {count} пр.\n"
            f"Осталось после этой: {remaining}\n\nНажми кнопку 👇",
            reply_markup=webapp_keyboard(token)
        )
        if remaining == 0:
            asyncio.create_task(remove_keyboard_later(context, user_id))

init_db()

# ── HTTP сервер для проверки токенов ──
async def check_token(request):
    token = request.rel_url.query.get("token", "")
    if not token:
        return web.json_response({"ok": False}, status=400)
    valid = validate_token(token)
    return web.json_response({"ok": valid})

async def run_web():
    server = web.Application()
    server.router.add_get("/check_token", check_token)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

async def main():
    await run_web()
    tg_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("buy", buy))
    tg_app.add_handler(CommandHandler("balance", balance))
    tg_app.add_handler(CallbackQueryHandler(handle_callback))
    tg_app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    tg_app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling()
        await asyncio.Event().wait()

asyncio.run(main())
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
