# bot.py
import os
import re
import asyncio
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ----------------- تنظیمات از ENV -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()  # باید عدد منفی یا مثبت به صورت رشته باشه
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()  # اختیاری، بدون @

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN پیدا نشد. لطفاً متغیر محیطی BOT_TOKEN را تنظیم کن.")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL پیدا نشد. لطفاً دیتابیس PostgreSQL در Railway اضافه کن و URL را ست کن.")
if not CHANNEL_ID:
    raise RuntimeError("CHANNEL_ID پیدا نشد. (آیدی کانال رو باید در متغیر محیطی CHANNEL_ID قرار بدی)")

CHANNEL_ID_INT = int(CHANNEL_ID)

bot = Bot(token=BOT_TOKEN, parse_mode="Markdown")
dp = Dispatcher(bot)

# global db pool
db_pool: asyncpg.pool.Pool | None = None

# ----------------- SQL ایجاد جدول‌ها -----------------
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

# ----------------- توابع DB -----------------
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    async with db_pool.acquire() as conn:
        # create tables
        for stmt in CREATE_TABLES_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                await conn.execute(s + ";")
    print("✅ DB initialized")


# ==================
# تابع اضافه کردن پست
# ==================
def add_post(message_id, title, content, hashtags):
    """
    یک پست جدید با هشتگ‌ها ذخیره می‌کند.
    """
    cur.execute(
        "INSERT INTO posts (message_id, title, content) VALUES (%s, %s, %s) RETURNING id",
        (message_id, title, content),
    )
    post_id = cur.fetchone()[0]

    # ثبت هشتگ‌ها
    for tag in hashtags:
        cur.execute(
            "INSERT INTO hashtags (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            (tag,),
        )
        hashtag_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO post_hashtags (post_id, hashtag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (post_id, hashtag_id),
        )

    conn.commit()
    return post_id

# ===========================
# گرفتن پست‌ها بر اساس هشتگ
# ===========================
def get_posts_by_hashtag(hashtag, limit=5):
    cur.execute("""
        SELECT p.title, p.content, p.message_id
        FROM posts p
        JOIN post_hashtags ph ON p.id = ph.post_id
        JOIN hashtags h ON ph.hashtag_id = h.id
        WHERE h.name = %s
        ORDER BY p.created_at DESC
        LIMIT %s
    """, (hashtag, limit))
    return cur.fetchall()

# ===========================
# جستجو در عناوین
# ===========================
def search_posts(keyword, limit=5):
    cur.execute("""
        SELECT title, content, message_id
        FROM posts
        WHERE title ILIKE %s
        ORDER BY created_at DESC
        LIMIT %s
    """, (f"%{keyword}%", limit))
    return cur.fetchall()

# ===========================
# عضویت کاربر در یک هشتگ
# ===========================
def subscribe_user(user_id, hashtag):
    cur.execute(
        "INSERT INTO hashtags (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
        (hashtag,),
    )
    hashtag_id = cur.fetchone()[0]

    cur.execute(
        "INSERT INTO subscriptions (user_id, hashtag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (user_id, hashtag_id),
    )
    conn.commit()
# ===========================
# لیست اشتراک‌های کاربر
# ===========================
def get_user_subscriptions(user_id):
    cur.execute("""
        SELECT h.name
        FROM subscriptions s
        JOIN hashtags h ON s.hashtag_id = h.id
        WHERE s.user_id = %s
    """, (user_id,))
    return [row[0] for row in cur.fetchall()]

# ===========================
# گرفتن کاربران مشترک یک هشتگ
# ===========================
def get_subscribers(hashtag):
    cur.execute("""
        SELECT s.user_id
        FROM subscriptions s
        JOIN hashtags h ON s.hashtag_id = h.id
        WHERE h.name = %s
    """, (hashtag,))
    return [row[0] for row in cur.fetchall()]

# ===========================
# محدود کردن تعداد پست‌ها
# ===========================
def cleanup_old_posts(max_posts=1000):
    cur.execute("""
        DELETE FROM posts
        WHERE id IN (
            SELECT id FROM posts
            ORDER BY created_at ASC
            OFFSET %s
        )
    """, (max_posts,))
    conn.commit()



async def get_or_create_hashtag(conn, name: str) -> int:
    # name assumed like "#استخدام"
    row = await conn.fetchrow("SELECT id FROM hashtags WHERE name = $1", name)
    if row:
        return row["id"]
    rec = await conn.fetchrow("INSERT INTO hashtags (name) VALUES ($1) RETURNING id", name)
    return rec["id"]

async def save_post_and_tags(message_id: int, title: str, content: str, tags: list[str]):
    """
    Insert post (if not exists) and link hashtags.
    Also enforce post limit (1000).
    """
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            rec = await conn.fetchrow(
                "INSERT INTO posts (message_id, title, content) VALUES ($1, $2, $3) ON CONFLICT (message_id) DO NOTHING RETURNING id",
                message_id, title, content
            )
            if rec:
                post_db_id = rec["id"]
            else:
                # already exists -> get id
                rec2 = await conn.fetchrow("SELECT id FROM posts WHERE message_id = $1", message_id)
                post_db_id = rec2["id"]

            # insert hashtags and relations
            for tag in tags:
                hid = await get_or_create_hashtag(conn, tag)
                await conn.execute(
                    "INSERT INTO post_hashtags (post_id, hashtag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    post_db_id, hid
                )

            # enforce 1000 posts limit
            total = await conn.fetchval("SELECT COUNT(*) FROM posts")
            limit = 1000
            if total > limit:
                to_remove = await conn.fetch(
                    "SELECT id FROM posts ORDER BY created_at ASC LIMIT $1", total - limit
                )
                ids = [r["id"] for r in to_remove]
                for pid in ids:
                    await conn.execute("DELETE FROM posts WHERE id = $1", pid)

    return

async def get_hashtags_for_post(post_db_id: int) -> list[str]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT h.name FROM hashtags h
            JOIN post_hashtags ph ON ph.hashtag_id = h.id
            WHERE ph.post_id = $1
            ORDER BY h.name
        """, post_db_id)
        return [r["name"] for r in rows]

async def get_post_db_row_by_message_id(message_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT id, message_id, title, content FROM posts WHERE message_id = $1", message_id)

async def search_posts_by_tag(tag_name: str, limit: int = 5):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.message_id, p.title FROM posts p
            JOIN post_hashtags ph ON ph.post_id = p.id
            JOIN hashtags h ON h.id = ph.hashtag_id
            WHERE h.name = $1
            ORDER BY p.created_at DESC
            LIMIT $2
        """, tag_name, limit)
        return rows

async def search_posts_by_keyword(keyword: str, limit: int = 5):
    kw = f"%{keyword}%"
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT message_id, title FROM posts
            WHERE title ILIKE $1
            ORDER BY created_at DESC
            LIMIT $2
        """, kw, limit)
        return rows

# subscriptions
async def add_subscription(user_id: int, tag_name: str):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            tag = await conn.fetchrow("SELECT id FROM hashtags WHERE name = $1", tag_name)
            if not tag:
                # if tag not exists yet, create it
                rec = await conn.fetchrow("INSERT INTO hashtags(name) VALUES($1) RETURNING id", tag_name)
                tag_id = rec["id"]
            else:
                tag_id = tag["id"]
            await conn.execute("INSERT INTO subscriptions (user_id, hashtag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", user_id, tag_id)

async def remove_subscription(user_id: int, tag_name: str):
    async with db_pool.acquire() as conn:
        tag = await conn.fetchrow("SELECT id FROM hashtags WHERE name = $1", tag_name)
        if tag:
            await conn.execute("DELETE FROM subscriptions WHERE user_id = $1 AND hashtag_id = $2", user_id, tag["id"])

async def get_user_subscriptions(user_id: int) -> list[str]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT h.name FROM hashtags h
            JOIN subscriptions s ON s.hashtag_id = h.id
            WHERE s.user_id = $1
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
            JOIN hashtags h ON h.id = s.hashtag_id
            WHERE h.name = $1
        """, tag_name)
        return [r["user_id"] for r in rows]

# ----------------- کمکی‌های ارسال -----------------
def make_hashtag_buttons(tag_list: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    for t in tag_list:
        kb.add(InlineKeyboardButton(t, callback_data=f"tag_search:{t}"))
    return kb

async def copy_post_to_user(user_id: int, from_chat_id: int, message_id: int, tags: list[str]):
    """
    سعی میکنیم خود پیام کانال رو کپی کنیم برای user و دکمه‌های هشتگ رو زیرش بفرستیم.
    اگر کپی پیام خطا داد (مثلا بلاک شده یا پیام مدیا داشت و ربات دسترسی نداشت)، عنوان+لینک می‌فرستیم.
    """
    try:
        kb = make_hashtag_buttons(tags)
        await bot.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id, reply_markup=kb)
    except Exception as e:
        # fallback: ارسال متن عنوان و لینک (اگر CHANNEL_USERNAME موجود باشه لینک میسازیم)
        link = None
        if CHANNEL_USERNAME:
            link = f"https://t.me/{CHANNEL_USERNAME}/{message_id}"
            text = f"📌 (پست کانال) \n\n🔗 {link}"
        else:
            text = f"📌 (پست کانال) شناسه پیام: `{message_id}`"
        try:
            await bot.send_message(user_id, text)
        except Exception as ex:
            print(f"❌ ارسال fallback به {user_id} ناموفق: {ex}")
    return

# ----------------- هندلر پست کانال -----------------
@dp.channel_post_handler(content_types=types.ContentTypes.ANY)
async def channel_post_handler(message: types.Message):
    # فقط متن/کپشن که شامل عنوان باشه رو پردازش می‌کنیم
    text = message.text or message.caption
    if not text:
        return

    # تعریف الگو: عنوان باید در خط اول و با emoji خاص (مثلاً 📌) شروع شده باشه.
    first_line = text.splitlines()[0].strip()
    if not first_line.startswith("📌"):
        # اگر نخواستید محدود به ایموجی باشه، می‌تونید این شرط رو بردارید
        return

    # استخراج عنوان و محتوا
    title = re.sub(r"^📌\s*", "", first_line).strip()
    content = "\n".join(text.splitlines()[1:]).strip()

    # استخراج هشتگ‌ها (کلماتی که با # شروع میشن)
    tags = re.findall(r"#\S+", text)
    # normalize tags: keep as-is (مثلاً '#استخدام')
    # ذخیره در DB
    await save_post_and_tags(message.message_id, title, content, tags)
    print(f"✅ ذخیره پست {message.message_id} - Title: {title} - Tags: {tags}")

    # ارسال خودکار برای مشترک‌ها
    # برای هر هشتگ، لیست مشترک‌ها بگیر و پست را کپی کن
    for tag in tags:
        subscribers = await get_subscribers_for_hashtag(tag)
        # unique subscribers to avoid duplicate sends if multiple tags overlap
        for uid in set(subscribers):
            try:
                # قبل از ارسال، می‌خواهیم دکمه‌های هشتگ رو هم زیر پیام ارسال کنیم.
                await copy_post_to_user(uid, CHANNEL_ID_INT, message.message_id, tags)
            except Exception as e:
                print(f"❌ ارسال خودکار به {uid} با خطا: {e}")

# ----------------- منو و جستجو -----------------
waiting_for_search: dict[int, bool] = {}

@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🔍 جستجو اطلاعیه/خبر", "🔔 دریافت خودکار اطلاعیه/خبر")
    await msg.answer(
        "سلام! من ربات اطلاع‌رسانی کافی‌نت هستم. یکی از گزینه‌ها را انتخاب کن:",
        reply_markup=kb,
        parse_mode="Markdown"  # Markdown قدیمی
    )

@dp.message_handler(lambda m: m.text == "🔍 جستجو اطلاعیه/خبر")
async def start_search_flow(msg: types.Message):
    waiting_for_search[msg.chat.id] = True
    await msg.answer("🔎 لطفاً کلیدواژهٔ جستجو را بفرست (جستجو فقط در عنوان‌ها انجام خواهد شد):")

@dp.message_handler(lambda m: m.chat.id in waiting_for_search)
async def handle_search_input(msg: types.Message):
    if not waiting_for_search.pop(msg.chat.id, None):
        return
    keyword = msg.text.strip()
    results = await search_posts_by_keyword(keyword, limit=5)
    if not results:
        await msg.answer("❌ موردی پیدا نشد.")
        return
    for r in results:
        message_id = r["message_id"]
        # get hashtags for the saved post
        post_row = await get_post_db_row_by_message_id(message_id)
        if post_row:
            # get post_id in posts table
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT id FROM posts WHERE message_id = $1", message_id)
                if row:
                    post_db_id = row["id"]
                    tags = await get_hashtags_for_post(post_db_id)
                else:
                    tags = []
        else:
            tags = []
        # try to copy original post to user with hashtag buttons
        await copy_post_to_user(msg.chat.id, CHANNEL_ID_INT, message_id, tags)

@dp.message_handler(lambda m: m.text == "🔔 دریافت خودکار اطلاعیه/خبر")
async def show_subscription_menu(msg: types.Message):
    # read all hashtags from DB (dynamic)
    all_tags = await get_all_hashtags()
    if not all_tags:
        await msg.answer("هنوز هیچ هشتگی در دیتابیس ثبت نشده است.")
        return

    user_tags = await get_user_subscriptions(msg.from_user.id)
    kb = InlineKeyboardMarkup(row_width=2)
    for tag in all_tags:
        status = "✅" if tag in user_tags else "❌"
        kb.add(InlineKeyboardButton(f"{status} {tag}", callback_data=f"toggle:{tag}"))
    await msg.answer("📌 دسته‌های موجود (برای فعال/غیرفعال کردن کلیک کن):", reply_markup=kb)

# ----------------- callback handlers -----------------
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("toggle:"))
async def callback_toggle_subscription(call: types.CallbackQuery):
    tag = call.data.split("toggle:")[1]
    user_id = call.from_user.id
    user_tags = await get_user_subscriptions(user_id)
    if tag in user_tags:
        await remove_subscription(user_id, tag)
        await call.answer(f"❌ اشتراک {tag} لغو شد")
    else:
        await add_subscription(user_id, tag)
        await call.answer(f"✅ اشتراک {tag} فعال شد")
    # update menu message
    all_tags = await get_all_hashtags()
    user_tags = await get_user_subscriptions(user_id)
    kb = InlineKeyboardMarkup(row_width=2)
    for t in all_tags:
        status = "✅" if t in user_tags else "❌"
        kb.add(InlineKeyboardButton(f"{status} {t}", callback_data=f"toggle:{t}"))
    try:
        await call.message.edit_text("📌 دسته‌های موجود (برای فعال/غیرفعال کردن کلیک کن):", reply_markup=kb)
    except Exception:
        # اگر ویرایش نشد، فقط پیام جدید بفرست
        await call.message.answer("منوی اشتراک‌ها به‌روز شد.", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("tag_search:"))
async def callback_tag_search(call: types.CallbackQuery):
    tag = call.data.split("tag_search:")[1]
    results = await search_posts_by_tag(tag, limit=5)
    if not results:
        await call.answer("هیچ پستی با این هشتگ پیدا نشد.", show_alert=True)
        return
    await call.answer(f"در حال ارسال {len(results)} پست اخیر با {tag} ...")
    for r in results:
        mid = r["message_id"]
        # get tags of that post to show as buttons
        row = await get_post_db_row_by_message_id(mid)
        if row:
            async with db_pool.acquire() as conn:
                pr = await conn.fetchrow("SELECT id FROM posts WHERE message_id = $1", mid)
                if pr:
                    tags = await get_hashtags_for_post(pr["id"])
                else:
                    tags = []
        else:
            tags = []
        await copy_post_to_user(call.from_user.id, CHANNEL_ID_INT, mid, tags)

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
