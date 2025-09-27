import os
import re
import asyncio
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup

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
"""

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

@dp.message_handler(lambda m: m.text.isdigit())
async def set_search_limit(msg: types.Message):
    n = int(msg.text.strip())
    if n < 1 or n > 20:
        await msg.answer("❌ لطفاً عددی بین 1 تا 20 وارد کنید")
        return
    user_search_limit[msg.from_user.id] = n
    await msg.answer(f"✅ تعداد پست در جستجو روی {n} تنظیم شد")

async def ensure_user_exists(user_id: int):
    user = await get_user_from_db(user_id)
    if not user:
        await add_user_to_db(user_id)


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

async def add_subscription(user_id: int, tag_name: str):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            tag = await conn.fetchrow("SELECT id FROM hashtags WHERE name=$1", tag_name)
            tag_id = tag["id"] if tag else (await conn.fetchrow("INSERT INTO hashtags(name) VALUES($1) RETURNING id", tag_name))["id"]
            await conn.execute("INSERT INTO subscriptions(user_id,hashtag_id) VALUES($1,$2) ON CONFLICT DO NOTHING", user_id, tag_id)

async def remove_subscription(user_id: int, tag_name: str):
    async with db_pool.acquire() as conn:
        tag = await conn.fetchrow("SELECT id FROM hashtags WHERE name=$1", tag_name)
        if tag:
            await conn.execute("DELETE FROM subscriptions WHERE user_id=$1 AND hashtag_id=$2", user_id, tag["id"])

async def get_user_subscriptions(user_id: int) -> list[str]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT h.name FROM hashtags h
            JOIN subscriptions s ON s.hashtag_id=h.id
            WHERE s.user_id=$1
            ORDER BY h.name
        """, user_id)
        return [r["name"] for r in rows]

async def get_all_hashtags() -> list[str]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM hashtags ORDER BY name")
        return [r["name"] for r in rows]

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
async def get_or_create_hashtag(conn, tag: str) -> int:
    rec = await conn.fetchrow(
        """
        INSERT INTO hashtags(name)
        VALUES($1)
        ON CONFLICT(name) DO UPDATE SET name=EXCLUDED.name
        RETURNING id
        """,
        tag
    )
    return rec["id"]

# ----------------- منو و جستجو -----------------
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    await ensure_user_exists(msg.from_user.id)

    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🔍 جستجو اطلاعیه/خبر")
    kb.add("🔔 دریافت خودکار اطلاعیه/خبر")
    kb.add("⚙️ تنظیمات")

    await msg.answer(
        "سلام 👋\nخوش اومدی! از منوی زیر یکی رو انتخاب کن:",
        reply_markup=kb
    )


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
@dp.message_handler(lambda m: m.text and "دریافت خودکار اطلاعیه" in m.text)
async def show_subscription_menu(msg: types.Message):
    user = await get_user_from_db(msg.from_user.id)
    if not user:
        await msg.answer("⚠️ لطفاً ابتدا /start را بزنید تا ثبت نام شوید.")
        return

    all_tags = await get_all_hashtags()
    if not all_tags:
        await msg.answer("هنوز هیچ هشتگی ثبت نشده است.")
        return

    user_tags = await get_user_subscriptions(msg.from_user.id)
    kb = InlineKeyboardMarkup(row_width=2)
    for t in all_tags:
        status = "✅" if t in user_tags else "❌"
        kb.add(InlineKeyboardButton(f"{status} {t}", callback_data=f"toggle:{t}"))

    await msg.answer("📌 دسته‌های موجود:", reply_markup=kb)


# --- هندلر callback برای تغییر اشتراک‌ها ---
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("toggle:"))
async def callback_toggle_subscription(call: types.CallbackQuery):
    tag = call.data.split("toggle:")[1]

    # ثبت کاربر اگر هنوز ثبت نشده باشد
    await ensure_user_exists(call.from_user.id)

    user_tags = await get_user_subscriptions(call.from_user.id)

    if tag in user_tags:
        await remove_subscription(call.from_user.id, tag)
        await call.answer(f"❌ اشتراک {tag} لغو شد")
    else:
        await add_subscription(call.from_user.id, tag)
        await call.answer(f"✅ اشتراک {tag} فعال شد")

    # آپدیت کیبورد بدون تغییر متن پیام
    all_tags = await get_all_hashtags()
    user_tags = await get_user_subscriptions(call.from_user.id)
    kb = InlineKeyboardMarkup(row_width=2)
    for t in all_tags:
        status = "✅" if t in user_tags else "❌"
        kb.add(InlineKeyboardButton(f"{status} {t}", callback_data=f"toggle:{t}"))

    await call.message.edit_reply_markup(reply_markup=kb)

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
