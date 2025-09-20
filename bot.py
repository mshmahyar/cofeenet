import os
import json
import asyncio
import feedparser
from aiogram import Bot, Dispatcher, executor, types

API_TOKEN = os.getenv("BOT_TOKEN", "").strip()

if not API_TOKEN:
    raise ValueError("❌ BOT_TOKEN تنظیم نشده یا خالیه!")

bot = Bot(token=API_TOKEN, parse_mode="Markdown")
dp = Dispatcher(bot)

# فایل اشتراک‌ها
SUB_FILE = "subscriptions.json"
if not os.path.exists(SUB_FILE):
    with open(SUB_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

# فایل آخرین لینک‌های ارسال‌شده
LAST_FILE = "last_seen.json"
if not os.path.exists(LAST_FILE):
    with open(LAST_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)


# -------------------- مدیریت فایل JSON --------------------
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_subscription(user_id, category):
    data = load_json(SUB_FILE)
    uid = str(user_id)
    if uid not in data:
        data[uid] = []
    if category not in data[uid]:
        data[uid].append(category)
    save_json(SUB_FILE, data)


def get_subscriptions():
    return load_json(SUB_FILE)


# -------------------- دسته‌ها و فیدها --------------------
CATEGORIES = {
    "استخدام": [
        "https://iranestekhdam.ir/feed",
        "https://www.e-estekhdam.com/feed",
    ],
    "دانشگاهی": [
        "https://iranmoshavere.com/feed",
    ],
    "سایر": [
        "https://www.medu.ir/fa/news/rss",  # نمونه: وزارت آموزش و پرورش
    ],
}


# -------------------- دستورات اصلی --------------------
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for cat in CATEGORIES.keys():
        kb.add(cat)
    kb.add("📌 اشتراک‌های من")
    await message.answer("سلام 👋 یکی از دسته‌ها رو انتخاب کن:", reply_markup=kb)


waiting_for_keyword = {}


@dp.message_handler(lambda msg: msg.text in CATEGORIES.keys())
async def choose_category(message: types.Message):
    category = message.text
    waiting_for_keyword[message.from_user.id] = category
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔔 عضویت در این دسته", callback_data=f"subscribe_{category}"))
    await message.answer(
        f"🔍 دسته «{category}» انتخاب شد.\n\nکلمه مورد نظر رو برای جستجو بفرست:",
        reply_markup=kb,
    )


@dp.message_handler(lambda msg: msg.chat.id in waiting_for_keyword)
async def handle_keyword(message: types.Message):
    user_id = message.from_user.id
    category = waiting_for_keyword[user_id]
    keyword = message.text.strip()

    urls = CATEGORIES.get(category, [])
    results = []

    for url in urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:10]:
            title = entry.title
            summary = getattr(entry, "summary", "")
            link = entry.link

            if keyword.lower() in title.lower() or keyword.lower() in summary.lower():
                results.append(
                    {
                        "title": title,
                        "summary": summary[:300] + "..." if summary else "بدون توضیحات",
                        "link": link,
                    }
                )

    if not results:
        await message.answer("❌ چیزی پیدا نشد، لطفا کلمه دیگه‌ای امتحان کن")
        return

    for item in results[:5]:  # فقط ۵ نتیجه
        text = f"📌 {item['title']}\n\n📝 {item['summary']}\n\n🔗 [مطالعه بیشتر]({item['link']})"
        await message.answer(text, disable_web_page_preview=True)


@dp.callback_query_handler(lambda c: c.data.startswith("subscribe_"))
async def subscribe_category(callback: types.CallbackQuery):
    category = callback.data.split("subscribe_")[1]
    add_subscription(callback.from_user.id, category)
    await callback.answer("✅ عضویت ثبت شد")
    await callback.message.answer(f"شما الان عضو اطلاعیه‌های «{category}» شدید.")


@dp.message_handler(lambda msg: msg.text == "📌 اشتراک‌های من")
async def my_subs(message: types.Message):
    subs = get_subscriptions().get(str(message.from_user.id), [])
    if not subs:
        await message.answer("📭 شما در هیچ دسته‌ای عضو نیستید.")
    else:
        await message.answer("📌 دسته‌های شما:\n" + "\n".join([f"🔔 {s}" for s in subs]))


# -------------------- زمان‌بندی ارسال خودکار --------------------
async def fetch_and_notify(category, urls, subscriptions):
    last_seen = load_json(LAST_FILE)
    seen_links = set(last_seen.get(category, []))

    new_posts = []
    for url in urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:5]:
            if entry.link not in seen_links:
                new_posts.append(entry)

    if not new_posts:
        return

    all_links = list(seen_links) + [p.link for p in new_posts]
    last_seen[category] = all_links[-20:]
    save_json(LAST_FILE, last_seen)

    for post in new_posts:
        text = f"📌 {post.title}\n\n📝 {getattr(post, 'summary', '')[:300]}...\n\n🔗 [مطالعه بیشتر]({post.link})"
        for user_id, cats in subscriptions.items():
            if category in cats:
                try:
                    await bot.send_message(int(user_id), text, disable_web_page_preview=True)
                except Exception as e:
                    print(f"❌ ارسال برای {user_id} ناموفق: {e}")


async def scheduler():
    while True:
        subs = get_subscriptions()
        for category, urls in CATEGORIES.items():
            await fetch_and_notify(category, urls, subs)
        await asyncio.sleep(1800)  # هر ۳۰ دقیقه


async def on_startup(dp):
    asyncio.create_task(scheduler())


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
