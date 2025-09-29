import os
import re
import asyncio
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup


class ServiceOrder(StatesGroup):
    waiting_for_docs = State()
    waiting_for_confirmation = State()

class AddService(StatesGroup):
    waiting_for_category = State()
    waiting_for_title = State()
    waiting_for_documents = State()
    waiting_for_price = State()

ADMIN_CHAT_ID = 7918162941


# ----------------- تنظیمات از ENV -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()  # اختیاری


if not BOT_TOKEN or not DATABASE_URL or not CHANNEL_ID:
    raise RuntimeError("لطفاً BOT_TOKEN, DATABASE_URL و CHANNEL_ID را در ENV ست کنید.")

CHANNEL_ID_INT = int(CHANNEL_ID)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

waiting_for_keyword: dict[int, bool] = {}
waiting_for_limit: dict[int, bool] = {}
user_search_limit: dict[int, int] = {}

#@dp.callback_query_handler()
#async def debug_all_callbacks(call: types.CallbackQuery):
    #print("📥 Callback received:", call.data)
    #await call.answer("دکمه کلیک شد ✅")

# ----------------- DB pool -----------------
db_pool: asyncpg.pool.Pool | None = None

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    message_id BIGINT UNIQUE,
    title TEXT,
    content TEXT,
    created_at TIMESTAMP DEFAULT now()
);
CREATE TABLE IF NOT EXISTS hashtags (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS post_hashtags (
    post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
    hashtag_id INTEGER REFERENCES hashtags(id) ON DELETE CASCADE,
    PRIMARY KEY (post_id, hashtag_id)
);
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id BIGINT,
    hashtag_id INTEGER REFERENCES hashtags(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, hashtag_id)
);
-- توی PostgreSQL اجرا کن
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TIMESTAMP DEFAULT now()
);
"""

SERVICES = {
    "خدمات خودرو": [
        "ثبت نام ایران خودرو", "ثبت نام سایپا", "ثبت نام بهمن موتور", "دیگر ثبت نام ها",
        "وکالتی کردن حساب", "استعلام قرعه کشی", "پرداخت مبلغ", "بیمه خودرو", "دیگر خدمات خودرویی"
    ],
    "خدمات کنکور": [
        "ثبت نام کنکور", "انتخاب رشته", "نتایج کنکور"
    ],
    "خدمات دانشگاه": [
        "ثبت نام دانشگاه", "تاییدیه تحصیلی", "سلامت روان", "نمونه سوال",
        "پروژه دانشجویی", "پایان نامه", "سامانه سجاد", "کارت ورود به جلسه",
        "دیگر خدمات دانشگاهی"
    ],
    "خدمات سجام و بورس": [
        "ثبت نام سجام", "سهام متوفیان", "سهام نوزاد", "سهام عدالت", "ثبت نام کارگزاری",
        "دیگر خدمات بورسی"
    ],
    "خدمات مالیاتی و اظهارنامه": [
        "اظهار نامه حقوقی", "اظهارنامه حقیقی", "اظهارنامه شراکتی", "اظهار نامه اجاره",
        "اظهارنامه ارزش افزوده", "مالیات خودرو", "مالیات بر ارث", "رفع مسدودی حساب متوفی",
        "دیگر خدمات مالیاتی"
    ],
    "ثبت نام وام": [
        "وام ازدواج", "وام فرزند", "وام مسکن", "وام اجاره (ودیعه)", "وام اشتغال", "دیگر وام ها"
    ],
    "خدمات ابلاغیه و ثنا": [
        "دریافت ابلاغیه", "اطلاع رسانی روند پرونده", "نوبت گیری قضایی",
        "پرداخت خدمات قضایی", "ثبت نام ثنا", "برگ ثتا", "تغییر رمز شخصی و موقت",
        "گواهی سوء پیشینه", "دیگر خدمات قضایی"
    ],
    "خدمات سخا و تعویض پلاک": [
        "ثبت نام سخا", "ثبت و احراز کد پستی", "خدمات نظام وظیفه",
        "استعلام کارت سوخت و پایان خدمت", "نوبت گیری تعویض پلاک", "نوبت گیری خدمات خودرو",
        "پرداخت مالیات و خلافی", "پرداخت عوارض", "وام سربازی", "دیگر خدمات انتظامی"
    ],
    "سامانه املاک و اجاره نامه": [
        "ثبت ملک", "ثبت محل اقامت", "ثبت اجاره نامه", "ثبت خرید و فروش",
        "ثبت نام وام ودیعه", "دیگر خدمات مسکن"
    ],
    "خدمات بیمه و تامین اجتماعی": [
        "ثبت نام تامین اجتماعی", "سوابق بیمه", "فیش حقوقی", "فیش بیمه",
        "گواهی کسر از اقساط", "مدیریت تحت تکفل", "بیمه سربازی", "کمک هزینه ازدواج",
        "وام تامین اجتماعی", "خدمات بیمه کشوری", "خدمات بیمه نیروهای مسلح",
        "خرید بیمه", "تمدید بیمه", "بیمه خودرو", "تخفیف بیمه",
        "دیگر خدمات بیمه و تامین"
    ],
    "دیگر خدمات": []
}

async def get_user_from_db(user_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)

async def add_user_to_db(user_id: int, username: str = None, first_name: str = None):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, first_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id, username, first_name
        )


async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    async with db_pool.acquire() as conn:
        for stmt in CREATE_TABLES_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                await conn.execute(s + ";")
    print("✅ DB initialized")

user_search_limit: dict[int,int] = {}

# گرفتن دسته‌بندی‌ها
async def get_all_categories():
    async with db_pool.acquire() as conn:
        return await conn.fetch("SELECT id, name FROM service_categories ORDER BY name")

# افزودن خدمت جدید
async def add_service_to_db(category_name, title, documents, price):
    async with db_pool.acquire() as conn:
        category = await conn.fetchrow("SELECT id FROM service_categories WHERE name=$1", category_name)
        if not category:
            raise ValueError("دسته‌بندی یافت نشد!")
        await conn.execute(
            "INSERT INTO services (category_id, title, documents, price) VALUES ($1, $2, $3, $4)",
            category["id"], title, documents, price
        )


@dp.message_handler(lambda m: m.text.isdigit())
async def set_search_limit(msg: types.Message):
    n = int(msg.text.strip())
    if n < 1 or n > 20:
        await msg.answer("❌ لطفاً عددی بین 1 تا 20 وارد کنید")
        return
    user_search_limit[msg.from_user.id] = n
    await msg.answer(f"✅ تعداد پست در جستجو روی {n} تنظیم شد")

async def ensure_user_exists(user: types.User):
    u = await get_user_from_db(user.id)
    if not u:
        await add_user_to_db(user.id, user.username, user.first_name)




# --- ساخت دکمه‌های هشتگ ---
def make_hashtag_buttons(tags: list[str]) -> InlineKeyboardMarkup:
    """
    ساخت کیبورد دکمه‌ای برای لیست هشتگ‌ها
    هر هشتگ یک دکمه است که callback اش 'tag_search:<tag>' خواهد بود
    """
    kb = InlineKeyboardMarkup(row_width=3)
    for t in tags:
        kb.insert(InlineKeyboardButton(t, callback_data=f"tag_search:{t}"))
    return kb

# ----------------- تعداد پست در هر جستجو -----------------
def get_user_search_limit(chat_id: int) -> int:
    # پیش‌فرض 5 تا پست برمی‌گردونه
    return user_search_limit.get(chat_id, 5)


async def search_posts_by_keyword(keyword: str, limit: int = 5):
    kw = f"%{keyword}%"
    async with db_pool.acquire() as conn:
        return await conn.fetch("""
            SELECT message_id, title 
            FROM posts
            WHERE title ILIKE $1
            ORDER BY created_at DESC
            LIMIT $2
        """, kw, limit)



async def search_posts_by_tag(tag_name: str, limit: int = 5):
    async with db_pool.acquire() as conn:
        return await conn.fetch("""
            SELECT p.message_id,p.title FROM posts p
            JOIN post_hashtags ph ON ph.post_id=p.id
            JOIN hashtags h ON h.id=ph.hashtag_id
            WHERE h.name=$1
            ORDER BY p.created_at DESC
            LIMIT $2
        """, tag_name, limit)


# --- تابع گرفتن هشتگ‌های یک پست ---
async def get_hashtags_for_post(post_db_id: int) -> list[str]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT h.name FROM hashtags h
            JOIN post_hashtags ph ON ph.hashtag_id = h.id
            WHERE ph.post_id=$1
            ORDER BY h.name
        """, post_db_id)
        return [r["name"] for r in rows]

# اضافه کردن اشتراک
async def add_subscription(user_id: int, tag_name: str):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            tag_row = await conn.fetchrow("SELECT id FROM hashtags WHERE name=$1", tag_name)
            tag_id = tag_row["id"] if tag_row else await get_or_create_hashtag(conn, tag_name)
            await conn.execute("""
                INSERT INTO subscriptions (user_id, hashtag_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id, hashtag_id) DO NOTHING
            """, user_id, tag_id)

# remove_subscription
async def remove_subscription(user_id: int, tag_name: str):
    async with db_pool.acquire() as conn:
        tag = await conn.fetchrow("SELECT id FROM hashtags WHERE name=$1", tag_name)
        if tag:
            await conn.execute("DELETE FROM subscriptions WHERE user_id=$1 AND hashtag_id=$2", user_id, tag["id"])


# get_user_subscriptions
async def get_user_subscriptions(user_id: int) -> list[str]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT h.name FROM subscriptions s
            JOIN hashtags h ON h.id = s.hashtag_id
            WHERE s.user_id=$1
            ORDER BY h.name
        """, user_id)
        return [r["name"] for r in rows]


# get_subscribers_for_hashtag
async def get_subscribers_for_hashtag(tag_name: str) -> list[int]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.user_id FROM subscriptions s
            JOIN hashtags h ON h.id = s.hashtag_id
            WHERE h.name=$1
        """, tag_name)
        return [r["user_id"] for r in rows]


async def get_subscribers_for_hashtag(tag_name: str) -> list[int]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.user_id FROM subscriptions s
            JOIN hashtags h ON h.id=s.hashtag_id
            WHERE h.name=$1
        """, tag_name)
        return [r["user_id"] for r in rows]

# ----------------- ارسال پست به کاربر -----------------
def make_hashtag_buttons(tag_list: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    for t in tag_list:
        kb.add(InlineKeyboardButton(t, callback_data=f"tag_search:{t}"))
    return kb

async def copy_post_to_user(user_id: int, from_chat_id: int, message_id: int, tags: list[str]):
    try:
        kb = make_hashtag_buttons(tags)
        await bot.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id, reply_markup=kb)
    except Exception:
        text = f"📌 شناسه پیام: `{message_id}`"
        await bot.send_message(user_id, text)

# ----------------- هندلر پست کانال -----------------
# هندلر برای پست‌های کانال
@dp.channel_post_handler(content_types=types.ContentTypes.ANY)
async def channel_post_handler(message: types.Message):
    text = message.text or message.caption
    if not text:
        return

    # شرط 📌 → اگر نمی‌خوای، این بخش رو کامنت کن
    first_line = text.splitlines()[0].strip()
    if not first_line.startswith("📌"):
        return

    # عنوان و محتوا
    title = re.sub(r"^📌\s*", "", first_line).strip()
    content = "\n".join(text.splitlines()[1:]).strip()

    # پیدا کردن هشتگ‌ها
    tags = re.findall(r"#\S+", text)

    # ذخیره در دیتابیس
    await save_post_and_tags(message.message_id, title, content, tags)

    # ارسال برای سابسکرایبرها
    for tag in tags:
        subs = await get_subscribers_for_hashtag(tag)
        for uid in set(subs):
            await copy_post_to_user(uid, CHANNEL_ID_INT, message.message_id, tags)

async def save_post_and_tags(message_id: int, title: str, content: str, tags: list[str]):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # ذخیره پست
            rec = await conn.fetchrow(
                """
                INSERT INTO posts(message_id, title, content)
                VALUES($1, $2, $3)
                ON CONFLICT(message_id) DO UPDATE 
                SET title=EXCLUDED.title, content=EXCLUDED.content
                RETURNING id
                """,
                message_id, title, content
            )
            post_db_id = rec["id"]

            # حذف پست‌های قدیمی بیش از 1000
            total = await conn.fetchval("SELECT COUNT(*) FROM posts")
            if total > 1000:
                to_remove = await conn.fetch(
                    "SELECT id FROM posts ORDER BY created_at ASC LIMIT $1",
                    total - 1000
                )
                for r in to_remove:
                    await conn.execute("DELETE FROM posts WHERE id=$1", r["id"])

            # ذخیره هشتگ‌ها
            for tag in tags:
                hid = await get_or_create_hashtag(conn, tag)
                await conn.execute(
                    """
                    INSERT INTO post_hashtags(post_id, hashtag_id)
                    VALUES($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    post_db_id, hid
                )

async def get_post_db_row_by_message_id(message_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id,message_id,title,content FROM posts WHERE message_id=$1",
            message_id
        )


# تابع گرفتن یا ساختن هشتگ
async def get_or_create_hashtag(conn, tag_name: str) -> int:
    rec = await conn.fetchrow("""
        INSERT INTO hashtags(name)
        VALUES($1)
        ON CONFLICT(name) DO UPDATE SET name=EXCLUDED.name
        RETURNING id
    """, tag_name)
    return rec["id"]

# ----------------- منو و جستجو -----------------
def main_menu_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🔍 جستجو اطلاعیه/خبر"))
    kb.add(KeyboardButton("🔔 دریافت خودکار اطلاعیه/خبر"))
    kb.add(KeyboardButton("⚙️ تنظیمات"))
    kb.add(KeyboardButton("🛠 سفارش خدمات"))
    kb.add(KeyboardButton("📝 ثبت نام"))  # دکمه ثبت نام
    if is_admin:
        kb.add("⚙️ مدیریت")
    return kb

@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    is_admin = msg.from_user.id in ADMINS
    await msg.answer("سلام 👋\nمنو را انتخاب کنید:", reply_markup=main_menu_keyboard())

# ----------------- هندلر ثبت‌نام -----------------
@dp.message_handler(lambda m: m.text and "ثبت" in m.text and "نام" in m.text)
async def register_user(msg: types.Message):
    async with db_pool.acquire() as conn:
        # بررسی وجود کاربر
        row = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1", msg.from_user.id)
        if row:
            await msg.answer("✅ شما قبلاً ثبت‌نام شده‌اید.")
            return

        # ثبت کاربر جدید
        await conn.execute(
            """
            INSERT INTO users(user_id, username, first_name, created_at)
            VALUES($1, $2, $3, NOW())
            """,
            msg.from_user.id,
            msg.from_user.username or "",
            msg.from_user.first_name or ""
        )
        await msg.answer("🎉 ثبت‌نام شما با موفقیت انجام شد!")






@dp.message_handler(lambda m: m.text.isdigit())
async def set_search_limit(msg: types.Message):
    n = int(msg.text.strip())
    if n < 1 or n > 20:
        await msg.answer("❌ لطفاً عددی بین 1 تا 20 وارد کنید")
        return
    user_search_limit[msg.from_user.id] = n
    await msg.answer(f"✅ تعداد پست در جستجو روی {n} تنظیم شد")

# --- جستجو ---
@dp.message_handler(lambda m: m.text == "🔍 جستجو اطلاعیه/خبر")
async def start_search_flow(msg: types.Message):
    waiting_for_keyword[msg.chat.id] = True
    await msg.answer("🔎 لطفاً کلیدواژهٔ جستجو را بفرست (جستجو فقط در عنوان‌ها انجام خواهد شد):")

# ===============================
# هندلر نمایش متن جستجو
#================================
@dp.message_handler(lambda m: m.chat.id in waiting_for_keyword)
async def handle_search_input(msg: types.Message):
    if not waiting_for_keyword.pop(msg.chat.id, None):
        return

    limit = user_search_limit.get(msg.chat.id, 5)
    results = await search_posts_by_keyword(msg.text.strip(), limit=limit)
    if not results:
        await msg.answer("❌ موردی پیدا نشد.")
        return

    for r in results:
        row = await get_post_db_row_by_message_id(r["message_id"])
        if not row:
            continue

        tags = await get_hashtags_for_post(row["id"])
        post_link = f"https://t.me/{CHANNEL_USERNAME}/{row['message_id']}"

        text = (
            f"📌 <b>{row['title']}</b>\n"
            f"🔗 <a href='{post_link}'>مشاهده در کانال</a>"
        )

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📖 متن کامل", callback_data=f"view:{row['message_id']}"))

        # اضافه کردن دکمه‌های هشتگ‌ها (اگر وجود داشته باشند)
        if tags:
            for t in tags:
                kb.add(InlineKeyboardButton(t, callback_data=f"tag_search:{t}"))

        await msg.answer(text, reply_markup=kb, parse_mode="HTML")
        
# ==============================
# اشتراک
# ==============================
@dp.message_handler(lambda m: m.text == "🔔 دریافت خودکار اطلاعیه/خبر")
async def show_subscription_menu(msg: types.Message):
    async with db_pool.acquire() as conn:
        # بررسی ثبت‌نام کاربر
        user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1", msg.from_user.id)
        if not user:
            await msg.answer("⚠️ لطفاً ابتدا در ربات ثبت‌نام کنید. (📝 ثبت‌نام در ربات)")
            return

        # دریافت همه هشتگ‌ها
        all_tags = await conn.fetch("SELECT id, name FROM hashtags ORDER BY name")
        if not all_tags:
            await msg.answer("هنوز هیچ هشتگی ثبت نشده است.")
            return

        # دریافت هشتگ‌های فعال کاربر
        user_tags_rows = await conn.fetch(
            "SELECT hashtag_id FROM subscriptions WHERE user_id=$1",
            msg.from_user.id
        )
        user_tags = {r["hashtag_id"] for r in user_tags_rows}

    # ساخت کیبورد
    kb = InlineKeyboardMarkup(row_width=2)
    for t in all_tags:
        status = "✅" if t["id"] in user_tags else "❌"
        kb.insert(InlineKeyboardButton(f"{status} {t['name']}", callback_data=f"toggle:{t['id']}"))

    kb.add(InlineKeyboardButton("ثبت نهایی ✅", callback_data="register"))

    await msg.answer("📌 دسته‌های موجود:", reply_markup=kb)


# هندلر تغییر وضعیت
@dp.callback_query_handler(lambda c: c.data.startswith("toggle:"))
async def toggle_subscription(callback: types.CallbackQuery):
    tag_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    async with db_pool.acquire() as conn:
        # بررسی وجود هشتگ
        tag = await conn.fetchrow("SELECT id, name FROM hashtags WHERE id=$1", tag_id)
        if not tag:
            await callback.answer("❌ هشتگ پیدا نشد!", show_alert=True)
            return

        # تغییر وضعیت
        exists = await conn.fetchrow(
            "SELECT 1 FROM subscriptions WHERE user_id=$1 AND hashtag_id=$2",
            user_id, tag_id
        )
        if exists:
            await conn.execute("DELETE FROM subscriptions WHERE user_id=$1 AND hashtag_id=$2", user_id, tag_id)
        else:
            await conn.execute("INSERT INTO subscriptions (user_id, hashtag_id) VALUES ($1, $2)", user_id, tag_id)

        # دریافت مجدد داده‌ها
        all_tags = await conn.fetch("SELECT id, name FROM hashtags ORDER BY name")
        user_tags_rows = await conn.fetch("SELECT hashtag_id FROM subscriptions WHERE user_id=$1", user_id)
        user_tags = {r["hashtag_id"] for r in user_tags_rows}

    # بازسازی کیبورد
    kb = InlineKeyboardMarkup(row_width=2)
    for t in all_tags:
        status = "✅" if t["id"] in user_tags else "❌"
        kb.insert(InlineKeyboardButton(f"{status} {t['name']}", callback_data=f"toggle:{t['id']}"))

    kb.add(InlineKeyboardButton("ثبت نهایی ✅", callback_data="register"))

    # آپدیت منو
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()


# --- تابع کمکی برای دریافت هشتگ‌های کاربر ---
async def get_user_subscriptions(user_id: int) -> list[str]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT h.name FROM hashtags h
            JOIN subscriptions s ON s.hashtag_id=h.id
            WHERE s.user_id=$1
            ORDER BY h.name
            """,
            user_id
        )
        return [r["name"] for r in rows]

# --- هندلر جستجو با هشتگ ---
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("tag_search:"))
async def callback_tag_search(call: types.CallbackQuery):
    tag = call.data.split("tag_search:")[1]
    limit = 5  # یا از get_user_search_limit(call.from_user.id) استفاده کن
    results = await search_posts_by_tag(tag, limit)
    if not results:
        await call.answer("هیچ پستی با این هشتگ پیدا نشد.", show_alert=True)
        return

    await call.answer(f"در حال ارسال {len(results)} پست اخیر با {tag} ...")
    for r in results:
        row = await get_post_db_row_by_message_id(r["message_id"])
        tags = await get_hashtags_for_post(row["id"]) if row else []
        await copy_post_to_user(call.from_user.id, CHANNEL_ID_INT, r["message_id"], tags)

# =======================================
# هندلر نمایش متن کامل
# =======================================
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("view:"))
async def callback_view_post(call: types.CallbackQuery):
    msg_id = int(call.data.split("view:")[1])
    row = await get_post_db_row_by_message_id(msg_id)
    if not row:
        await call.answer("❌ پست پیدا نشد.", show_alert=True)
        return

    text = f"📌 <b>{row['title']}</b>\n\n{row['content']}"
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()


    limit = user_search_limit.get(msg.chat.id, 5)
    results = await search_posts_by_keyword(msg.text.strip(), limit=limit)
    if not results:
        await msg.answer("❌ موردی پیدا نشد.")
        return

    for r in results:
        row = await get_post_db_row_by_message_id(r["message_id"])
        tags = await get_hashtags_for_post(row["id"]) if row else []
        await copy_post_to_user(msg.chat.id, CHANNEL_ID_INT, r["message_id"], tags)


# --- منوی اشتراک ---
# نمایش منوی اشتراک
@dp.message_handler(lambda m: m.text == "🔔 دریافت خودکار اطلاعیه/خبر")
async def show_subscription_menu(msg: types.Message):
    async with db_pool.acquire() as conn:
        # بررسی ثبت‌نام کاربر
        user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1", msg.from_user.id)
        if not user:
            await msg.answer("⚠️ لطفاً ابتدا در ربات ثبت‌نام کنید. (📝 ثبت‌نام در ربات)")
            return

        # دریافت تمام هشتگ‌ها
        all_tags = await conn.fetch("SELECT id, name FROM hashtags ORDER BY name")
        if not all_tags:
            await msg.answer("هنوز هیچ هشتگی ثبت نشده است.")
            return

        # دریافت هشتگ‌های سابسکرایب‌شده کاربر
        user_tags_rows = await conn.fetch(
            """
            SELECT hashtag_id
            FROM subscriptions
            WHERE user_id=$1
            """,
            msg.from_user.id
        )
        user_tags = {r["hashtag_id"] for r in user_tags_rows}

    # ساخت کیبورد شیشه‌ای
    kb = InlineKeyboardMarkup(row_width=2)
    for t in all_tags:
        status = "✅" if t["id"] in user_tags else "❌"
        kb.insert(InlineKeyboardButton(f"{status} {t['name']}", callback_data=f"toggle:{t['id']}"))

    kb.add(InlineKeyboardButton("ثبت نهایی ✅", callback_data="register"))

    await msg.answer("📌 دسته‌های موجود:", reply_markup=kb)


# هندلر تغییر وضعیت هشتگ‌ها
@dp.callback_query_handler(lambda c: c.data.startswith("toggle:"))
async def toggle_subscription(callback: types.CallbackQuery):
    tag_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    async with db_pool.acquire() as conn:
        exists = await conn.fetchrow(
            "SELECT 1 FROM subscriptions WHERE user_id=$1 AND hashtag_id=$2",
            user_id, tag_id
        )
        if exists:
            await conn.execute(
                "DELETE FROM subscriptions WHERE user_id=$1 AND hashtag_id=$2",
                user_id, tag_id
            )
        else:
            await conn.execute(
                "INSERT INTO subscriptions (user_id, hashtag_id) VALUES ($1, $2)",
                user_id, tag_id
            )

        # دریافت مجدد وضعیت هشتگ‌ها
        all_tags = await conn.fetch("SELECT id, name FROM hashtags ORDER BY name")
        user_tags_rows = await conn.fetch(
            "SELECT hashtag_id FROM subscriptions WHERE user_id=$1", user_id
        )
        user_tags = {r["hashtag_id"] for r in user_tags_rows}

    # بازسازی کیبورد
    kb = InlineKeyboardMarkup(row_width=2)
    for t in all_tags:
        status = "✅" if t["id"] in user_tags else "❌"
        kb.insert(InlineKeyboardButton(f"{status} {t['name']}", callback_data=f"toggle:{t['id']}"))

    kb.add(InlineKeyboardButton("ثبت نهایی ✅", callback_data="register"))

    # فقط منو رو آپدیت کن
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()





@dp.callback_query_handler(lambda c: c.data and c.data.startswith("tag_search:"))
async def callback_tag_search(call: types.CallbackQuery):
    tag = call.data.split("tag_search:")[1]
    limit = get_user_search_limit(call.from_user.id)
    results = await search_posts_by_tag(tag, limit)
    if not results:
        await call.answer("هیچ پستی با این هشتگ پیدا نشد.", show_alert=True)
        return
    await call.answer(f"در حال ارسال {len(results)} پست اخیر با {tag} ...")
    for r in results:
        row = await get_post_db_row_by_message_id(r["message_id"])
        tags = await get_hashtags_for_post(row["id"]) if row else []
        await copy_post_to_user(call.from_user.id, CHANNEL_ID_INT, r["message_id"], tags)

# ========================
# سفارش خدمات
# ========================
@dp.message_handler(lambda m: m.text == "🛠 سفارش خدمات")
async def show_services_menu(msg: types.Message):
    kb = InlineKeyboardMarkup(row_width=2)
    for category in SERVICES.keys():
        kb.add(InlineKeyboardButton(category, callback_data=f"service_cat:{category}"))
    await msg.answer("📂 دسته‌بندی خدمات:", reply_markup=kb)

# ========================
# انتخاب دسته‌بندی
# ========================
@dp.callback_query_handler(lambda c: c.data.startswith("service_cat:"))
async def show_service_items(call: types.CallbackQuery):
    category = call.data.split(":", 1)[1]
    kb = InlineKeyboardMarkup(row_width=2)
    for item in SERVICES[category]:
        kb.add(InlineKeyboardButton(item, callback_data=f"service_item:{item}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="back_to_services"))
    await call.message.edit_text(f"📌 خدمات در دسته‌ی {category}:", reply_markup=kb)
    await call.answer()

# ========================
# انتخاب یک خدمت
# ========================
@dp.callback_query_handler(lambda c: c.data.startswith("service_item:"))
async def request_service(call: types.CallbackQuery):
    service = call.data.split(":", 1)[1]

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📤 ارسال مدارک", callback_data=f"send_docs:{service}"))

    await call.message.answer(
        f"✅ شما خدمت «{service}» را انتخاب کردید.\n\n"
        "📋 مدارک و اطلاعات مورد نیاز:\n"
        "1️⃣ کارت ملی\n2️⃣ شناسنامه\n3️⃣ فرم تکمیل‌شده مربوطه\n\n"
        "لطفاً پس از آماده‌سازی مدارک دکمه زیر را بزنید.",
        reply_markup=kb
    )
    await call.answer()

# ========================
# ارسال مدارک
# ========================
@dp.callback_query_handler(lambda c: c.data.startswith("send_docs:"))
async def start_sending_docs(call: types.CallbackQuery, state: FSMContext):
    service = call.data.split(":", 1)[1]
    await state.update_data(service_name=service, docs=[])
    
    await call.message.answer(
        f"📤 لطفاً مدارک و اطلاعات لازم برای خدمت «{service}» را ارسال کنید.\n"
        "📝 هر پیام می‌تواند حاوی بخشی از مدارک باشد.\n"
        "✅ پس از ارسال تمام مدارک، دکمه «درخواست نهایی» را بزنید."
    )

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📝 درخواست نهایی", callback_data="finalize_order"))
    await call.message.answer("⏺️ دکمه زیر را پس از آماده شدن مدارک بزنید:", reply_markup=kb)

    await state.set_state(ServiceOrder.waiting_for_docs)

@dp.message_handler(state=ServiceOrder.waiting_for_docs, content_types=types.ContentTypes.ANY)
async def collect_docs(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    docs = data.get("docs", [])
    docs.append(msg)
    await state.update_data(docs=docs)
    await msg.answer("✅ مدارک دریافت شد. اگر تمام شد، دکمه «درخواست نهایی» را بزنید.")

@dp.callback_query_handler(lambda c: c.data == "finalize_order", state=ServiceOrder.waiting_for_docs)
async def finalize_order(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    service = data["service_name"]
    docs = data["docs"]
    user_id = call.from_user.id

    # ساخت یک order_id ساده
    import random, string
    order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

    # ارسال مدارک به مدیر
    for msg in docs:
        if msg.content_type == "text":
            await bot.send_message(ADMIN_CHAT_ID,
                f"🆔 سفارش: {order_id}\n👤 کاربر: {user_id}\n\n{msg.text}"
            )
        elif msg.content_type in ["photo", "document", "video"]:
            # ارسال فایل‌ها به همراه متن (اگر وجود داشته باشه)
            caption = f"🆔 سفارش: {order_id}\n👤 کاربر: {user_id}"
            if msg.caption:
                caption += f"\n\n{msg.caption}"
            if msg.content_type == "photo":
                await bot.send_photo(ADMIN_CHAT_ID, msg.photo[-1].file_id, caption=caption)
            elif msg.content_type == "document":
                await bot.send_document(ADMIN_CHAT_ID, msg.document.file_id, caption=caption)
            elif msg.content_type == "video":
                await bot.send_video(ADMIN_CHAT_ID, msg.video.file_id, caption=caption)

    # ایجاد کیبورد اتمام خدمت برای مدیر
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ اتمام خدمت", callback_data=f"complete_order:{order_id}:{user_id}"))
    await bot.send_message(ADMIN_CHAT_ID, f"📌 سفارش {order_id} آماده بررسی است.", reply_markup=kb)

    await call.message.answer(f"🎉 سفارش شما با موفقیت ثبت شد! کد سفارش شما: {order_id}")
    await state.clear()

@dp.callback_query_handler(lambda c: c.data.startswith("complete_order:"))
async def complete_order(call: types.CallbackQuery):
    _, order_id, user_id = call.data.split(":")
    await call.message.edit_text(f"✅ سفارش {order_id} توسط مدیر تکمیل شد.")
    # در صورت استفاده از دیتابیس، اینجا سفارش حذف شود.


# ========================
# مدیریت
# ========================
@dp.message_handler(lambda m: m.text == "⚙️ مدیریت" and m.from_user.id in ADMINS)
async def admin_menu(msg: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ افزودن خدمات", "🗂 مدیریت خدمات")
    kb.add("🔙 بازگشت به منو اصلی")
    await msg.answer("بخش مدیریت:", reply_markup=kb)


# ========================
# انتخاب دسته بندی
# ========================
@dp.message_handler(lambda m: m.text == "➕ افزودن خدمات", user_id=ADMINS)
async def add_service_start(msg: types.Message):
    # گرفتن دسته‌بندی‌ها از دیتابیس
    cats = await db.fetch("SELECT * FROM service_categories")
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    for c in cats:
        kb.add(c["name"])
    kb.add("🔙 انصراف")
    await AddService.waiting_for_category.set()
    await msg.answer("یک دسته‌بندی انتخاب کنید:", reply_markup=kb)

# ========================
# عنوان خدمت
# ========================
@dp.message_handler(state=AddService.waiting_for_category)
async def add_service_category(msg: types.Message, state: FSMContext):
    cat_name = msg.text.strip()
    category = await db.fetchrow("SELECT * FROM service_categories WHERE name=$1", cat_name)
    if not category:
        await msg.answer("❌ دسته‌بندی معتبر نیست. دوباره انتخاب کنید.")
        return
    await state.update_data(category_id=category["id"])
    await AddService.waiting_for_title.set()
    await msg.answer("عنوان خدمت را وارد کنید:")

# ========================
# مدارک لازم
# ========================
@dp.message_handler(state=AddService.waiting_for_title)
async def add_service_title(msg: types.Message, state: FSMContext):
    await state.update_data(title=msg.text.strip())
    await AddService.waiting_for_documents.set()
    await msg.answer("مدارک لازم را وارد کنید (مثال: کارت ملی، شناسنامه و ...):")

# ========================
# ثبت خدمت
# ========================
@dp.message_handler(state=AddService.waiting_for_documents)
async def add_service_docs(message: types.Message, state: FSMContext):
    await state.update_data(documents=message.text.strip())
    await message.answer("💰 هزینه تقریبی خدمت را وارد کنید (به تومان):")
    await AddService.waiting_for_price.set()

@dp.message_handler(state=AddService.waiting_for_documents)
async def add_service_docs(message: types.Message, state: FSMContext):
    await state.update_data(documents=message.text.strip())
    await message.answer("💰 هزینه تقریبی خدمت را وارد کنید (به تومان):")
    await AddService.waiting_for_price.set()


# --- تنظیمات تعداد پست ---
@dp.message_handler(lambda m: m.text == "⚙️ تنظیمات")
async def show_settings_menu(msg: types.Message):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🔢 تعداد پست در هر جستجو", callback_data="set_search_limit"))
    await msg.answer("⚙️ تنظیمات ربات:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "set_search_limit")
async def callback_set_search_limit(call: types.CallbackQuery):
    waiting_for_limit[call.from_user.id] = True
    await call.message.answer("لطفاً عدد موردنظر برای تعداد پست در هر جستجو را بفرستید (مثلاً 5):")
    await call.answer()

@dp.message_handler(lambda m: waiting_for_limit.get(m.chat.id))
async def handle_set_search_limit(msg: types.Message):
    try:
        val = int(msg.text.strip())
        if val < 1 or val > 50:
            await msg.answer("❌ عدد باید بین 1 تا 50 باشد.")
            return
        user_search_limit[msg.chat.id] = val
        await msg.answer(f"✅ تعداد پست در هر جستجو به {val} تغییر کرد.")
        waiting_for_limit.pop(msg.chat.id, None)
    except ValueError:
        await msg.answer("❌ لطفاً یک عدد معتبر وارد کنید.")

# ----------------- startup/shutdown -----------------
async def on_startup(dispatcher):
    await init_db()
    print("بوت شروع شد.")

async def on_shutdown(dispatcher):
    if db_pool:
        await db_pool.close()
    session = await bot.get_session()
    await session.close()
    print("بوت خاموش شد.")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup, on_shutdown=on_shutdown)
