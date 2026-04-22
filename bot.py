from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, WebAppInfo, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters, CallbackQueryHandler
import sqlite3, secrets, time, asyncio

TELEGRAM_TOKEN = "8298157683:AAG8-TLkM4hpNZdOocWRqEr7BywKEc3rea0"
WEB_APP_URL = "https://steady-brioche-e0b7ee.netlify.app/"
STARS_1 = 25
STARS_5 = 100
STARS_MONTH = 220

# ── Белый список (username без @) ──
WHITELIST = {
    "riavlw",
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

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("buy", buy))
app.add_handler(CommandHandler("balance", balance))
app.add_handler(CallbackQueryHandler(handle_callback))
app.add_handler(PreCheckoutQueryHandler(pre_checkout))
app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
app.run_polling()
