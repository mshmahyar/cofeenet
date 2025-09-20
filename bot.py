import os
import feedparser
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

# گرفتن توکن از Railway → Environment Variables
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# ذخیره انتخاب بخش کاربر
user_section = {}

# --- تابع جستجو در RSS ---
def search_rss(feed_url, keyword):
    feed = feedparser.parse(feed_url)
    for entry in feed.entries[:10]:
        if keyword.lower() in entry.title.lower() or keyword.lower() in entry.summary.lower():
            return {
                "title": entry.title,
                "summary": entry.summary[:300] + "...",
                "link": entry.link
            }
    return None

# --- جستجو در سنجش ---
def search_sanjesh(keyword):
    url = "https://www.sanjesh.org/"
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")

    items = soup.find_all("a")
    for item in items:
        title = item.get_text(strip=True)
        link = item.get("href")
        if title and keyword in title:
            return {
                "title": title,
                "summary": title,
                "link": url + link if link.startswith("/") else link
            }
    return None

# --- جستجو در ایران مشاوره ---
def search_iranmoshavere(keyword):
    url = "https://iranmoshavere.com/"
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")

    items = soup.find_all("a")
    for item in items:
        title = item.get_text(strip=True)
        link = item.get("href")
        if title and keyword in title:
            return {
                "title": title,
                "summary": title,
                "link": link
            }
    return None

# --- جستجو در gov.ir (سایر اطلاعیه‌ها) ---
def search_gov(keyword):
    url = "https://www.gov.ir/fa/news"
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")

    items = soup.find_all("a")
    for item in items:
        title = item.get_text(strip=True)
        link = item.get("href")
        if title and keyword in title:
            return {
                "title": title,
                "summary": title,
                "link": "https://www.gov.ir" + link if link.startswith("/") else link
            }
    return None

# --- استارت ---
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📢 اطلاعیه استخدامی", "🎓 اطلاعیه دانشگاهی", "🌐 سایر اطلاعیه‌ها")
    await message.answer("سلام 👋 به ربات کافی‌نت خوش اومدی!\nیکی از بخش‌ها رو انتخاب کن:", reply_markup=kb)

# --- انتخاب بخش ---
@dp.message_handler(lambda m: m.text in ["📢 اطلاعیه استخدامی", "🎓 اطلاعیه دانشگاهی", "🌐 سایر اطلاعیه‌ها"])
async def choose_section(message: types.Message):
    user_section[message.chat.id] = message.text
    await message.answer("🔎 لطفا کلمه مورد نظر رو بفرست (مثلا: کنکور، بانک، آموزش و پرورش...)")

# --- جستجو بر اساس بخش انتخاب‌شده ---
@dp.message_handler()
async def handle_query(message: types.Message):
    section = user_section.get(message.chat.id, None)
    query = message.text.strip()

    result = None

    if section == "📢 اطلاعیه استخدامی":
        result = search_rss("https://iranestekhdam.ir/feed", query)
        if not result:
            result = search_rss("https://www.e-estekhdam.com/feed", query)

    elif section == "🎓 اطلاعیه دانشگاهی":
        result = search_sanjesh(query)
        if not result:
            result = search_iranmoshavere(query)

    elif section == "🌐 سایر اطلاعیه‌ها":
        result = search_gov(query)

    if result:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🌐 مطالعه بیشتر", url=result["link"]))
        await message.answer(
            f"📌 {result['title']}\n\n🔎 {result['summary']}",
            reply_markup=kb
        )
    else:
        await message.answer("❌ چیزی پیدا نشد، لطفا کلمه دیگه‌ای امتحان کن.")

# --- اجرای ربات ---
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
