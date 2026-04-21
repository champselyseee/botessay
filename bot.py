from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters, CallbackQueryHandler
import sqlite3, secrets, time

TELEGRAM_TOKEN = "8298157683:AAG8-TLkM4hpNZdOocWRqEr7BywKEc3rea0"
WEB_APP_URL = "https://incomparable-mooncake-248c44.netlify.app"
STARS_PRICE = 25

# ── Белый список (username без @) ──
WHITELIST = {
    "riavlw",       # замени на свой username
}

# ── База данных ──
def init_db():
    con = sqlite3.connect("users.db")
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            free_used INTEGER DEFAULT 0,
            paid_checks INTEGER DEFAULT 0
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
        con.execute("INSERT INTO users VALUES (?, ?, 0, 0)", (user_id, username))
        con.commit()
        row = con.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    con.close()
    return {"user_id": row[0], "username": row[1], "free_used": row[2], "paid_checks": row[3]}

def use_free_check(user_id: int):
    con = sqlite3.connect("users.db")
    con.execute("UPDATE users SET free_used = 1 WHERE user_id = ?", (user_id,))
    con.commit()
    con.close()

def add_paid_check(user_id: int):
    con = sqlite3.connect("users.db")
    con.execute("UPDATE users SET paid_checks = paid_checks + 1 WHERE user_id = ?", (user_id,))
    con.commit()
    con.close()

def use_paid_check(user_id: int):
    con = sqlite3.connect("users.db")
    con.execute("UPDATE users SET paid_checks = paid_checks - 1 WHERE user_id = ?", (user_id,))
    con.commit()
    con.close()

def create_token(user_id: int) -> str:
    token = secrets.token_hex(16)
    con = sqlite3.connect("users.db")
    con.execute("INSERT INTO tokens VALUES (?, ?, ?, 0)", (token, user_id, int(time.time())))
    con.commit()
    con.close()
    return token

def validate_token(token: str) -> bool:
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

def send_webapp_button(token: str) -> ReplyKeyboardMarkup:
    url = f"{WEB_APP_URL}?token={token}"
    return ReplyKeyboardMarkup(
        [[KeyboardButton("✍️ Открыть проверку", web_app=WebAppInfo(url=url))]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# ── /start ──
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username = user.username or ""
    data = get_user(user.id, username)

    # Белый список
    if is_whitelisted(username):
        token = create_token(user.id)
        await update.message.reply_text(
            "👋 Привет! У тебя безлимитный доступ.\n\nНажми кнопку ниже 👇",
            reply_markup=send_webapp_button(token)
        )
        return

    # Бесплатная проверка
    if not data["free_used"]:
        token = create_token(user.id)
        use_free_check(user.id)
        await update.message.reply_text(
            "👋 Привет! Тебе доступна 1 бесплатная проверка.\n\nНажми кнопку ниже 👇",
            reply_markup=send_webapp_button(token)
        )
        return

    # Есть оплаченные проверки
    if data["paid_checks"] > 0:
        token = create_token(user.id)
        use_paid_check(user.id)
        await update.message.reply_text(
            f"✅ У тебя {data['paid_checks']} проверок. Открываю 👇",
            reply_markup=send_webapp_button(token)
        )
        return

    # Нет доступа — предлагаем оплату
    await show_payment_menu(update, context)

async def show_payment_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💫 1 проверка — 25 Stars", callback_data="buy_stars_1")],
        [InlineKeyboardButton("💫 5 проверок — 100 Stars", callback_data="buy_stars_5")],
        [InlineKeyboardButton("💳 Оплата картой (скоро)", callback_data="buy_card")],
    ])
    text = (
        "🔒 Бесплатная проверка использована.\n\n"
        "Выбери способ оплаты:"
    )
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)

# ── Inline кнопки ──
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "buy_stars_1":
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title="1 проверка ЕГЭ",
            description="Одна проверка сочинения, эссе или письма по критериям ЕГЭ 2026",
            payload="stars_1",
            currency="XTR",
            prices=[LabeledPrice("1 проверка", STARS_PRICE)],
        )

    elif query.data == "buy_stars_5":
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title="5 проверок ЕГЭ",
            description="Пять проверок сочинений, эссе или писем по критериям ЕГЭ 2026",
            payload="stars_5",
            currency="XTR",
            prices=[LabeledPrice("5 проверок", STARS_PRICE * 4)],  # скидка
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

    count = 5 if payload == "stars_5" else 1
    for _ in range(count):
        add_paid_check(user_id)

    # Сразу выдаём токен на одну проверку
    token = create_token(user_id)
    data = get_user(user_id)
    remaining = data["paid_checks"] - 1
    use_paid_check(user_id)

    await update.message.reply_text(
        f"✅ Оплата прошла! Куплено проверок: {count}\n"
        f"Осталось после этой: {remaining}\n\nНажми кнопку 👇",
        reply_markup=send_webapp_button(token)
    )

# ── /buy — купить ещё ──
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_payment_menu(update, context)

# ── /balance — сколько проверок осталось ──
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user(user.id, user.username or "")
    if is_whitelisted(user.username or ""):
        await update.message.reply_text("👑 У тебя безлимитный доступ.")
        return
    await update.message.reply_text(
        f"📊 Твой баланс: {data['paid_checks']} проверок\n\n"
        f"Купить ещё → /buy"
    )

# ── /check — получить ссылку если есть баланс ──
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

init_db()

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("buy", buy))
app.add_handler(CommandHandler("balance", balance))
app.add_handler(CommandHandler("check", check))
app.add_handler(CallbackQueryHandler(handle_callback))
app.add_handler(PreCheckoutQueryHandler(pre_checkout))
app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
app.run_polling()