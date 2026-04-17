import requests
import json
import time
import os
import hashlib
from datetime import datetime

# ─── הגדרות ───────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 3600))
SEEN_FILE = "seen_ads.json"

MAX_PRICE = 85000
MIN_YEAR = 2020
MAX_KM = 100000

SEARCHES = [
    {
        "display": "סקודה קאמיק",
        "url": "https://gw.yad2.co.il/feed-search-legacy/vehicles/cars?manufacturer=54&model=1388&yearFrom=2020&priceTo=85000&kmTo=100000&page=1&rows=40"
    },
    {
        "display": "סקודה קארוק",
        "url": "https://gw.yad2.co.il/feed-search-legacy/vehicles/cars?manufacturer=54&model=1389&yearFrom=2020&priceTo=85000&kmTo=100000&page=1&rows=40"
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9",
    "Referer": "https://www.yad2.co.il/vehicles/cars",
    "Origin": "https://www.yad2.co.il",
    "mobile-app": "false",
}

def send_telegram(message, photo_url=None):
    try:
        if photo_url:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            data = {"chat_id": CHAT_ID, "photo": photo_url, "caption": message, "parse_mode": "HTML"}
            r = requests.post(url, data=data, timeout=10)
            if r.status_code != 200:
                send_telegram(message)
                return
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": False}
            requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"שגיאה בטלגרם: {e}")

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def fetch_yad2(search):
    results = []
    session = requests.Session()
    try:
        session.get("https://www.yad2.co.il/vehicles/cars", headers=HEADERS, timeout=15)
        time.sleep(2)
        resp = session.get(search["url"], headers=HEADERS, timeout=15)
        print(f"  Status: {resp.status_code}, Size: {len(resp.text)} bytes")
        if resp.status_code != 200:
            return results
        data = resp.json()
        items = data.get("data", {}).get("feed", {}).get("feed_items", [])
        print(f"  פריטים: {len(items)}")
        for item in items:
            if item.get("type") != "ad":
                continue
            try:
                ad_id = str(item.get("id", ""))
                if not ad_id:
                    continue
                year = item.get("year", 0)
                km = item.get("kilometers", 0)
                price = item.get("price", 0)
                city = item.get("city", "לא צוין")
                hand = item.get("hand", "")
                if price and int(price) > MAX_PRICE:
                    continue
                if km and int(km) > MAX_KM:
                    continue
                if year and int(str(year)) < MIN_YEAR:
                    continue
                km_fmt = f"{int(km):,} ק\"מ" if km else "לא צוין"
                price_fmt = f"₪{int(price):,}" if price else "לא צוין"
                hand_fmt = f"יד {hand}" if hand else ""
                photo = None
                images = item.get("images", {})
                if isinstance(images, dict) and images:
                    first_key = next(iter(images))
                    img_list = images[first_key]
                    if isinstance(img_list, list) and img_list:
                        photo = img_list[0].get("src")
                results.append({
                    "id": ad_id,
                    "display": search["display"],
                    "year": year,
                    "km": km_fmt,
                    "price": price_fmt,
                    "city": city,
                    "hand": hand_fmt,
                    "url": f"https://www.yad2.co.il/item/{ad_id}",
                    "photo": photo,
                })
            except Exception as e:
                print(f"  שגיאה בפריט: {e}")
    except Exception as e:
        print(f"  שגיאה בסריקה: {e}")
    return results

def send_alert(car):
    city = car.get("city", "")
    near_flag = ""
    if any(x in city for x in ["חיפה", "קריית", "קריות", "נשר", "טירת", "זכרון", "עתלית", "פרדס חנה", "בנימינה", "עכו", "נהריה"]):
        near_flag = " 📍 <b>קרוב אליך!</b>"
    msg = (
        f"🚗 <b>רכב חדש נמצא!</b>\n\n"
        f"🏷️ <b>{car['display']}</b>\n"
        f"📅 שנה: <b>{car['year']}</b>\n"
        f"📏 {car['km']}\n"
        f"💰 מחיר: <b>{car['price']}</b>\n"
        f"📍 עיר: {car['city']}{near_flag}\n"
        f"✋ {car['hand']}\n\n"
        f"🔗 <a href=\"{car['url']}\">לחץ לצפייה במודעה</a>\n\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    send_telegram(msg, photo_url=car.get("photo"))

def main():
    print(f"🚗 Car Hunter מופעל | סריקה כל {CHECK_INTERVAL//60} דקות")
    send_telegram(
        "🚗 <b>Car Hunter מופעל!</b>\n\n"
        f"🔍 מחפש: סקודה קאמיק / קארוק\n"
        f"💰 עד ₪{MAX_PRICE:,}\n"
        f"📅 {MIN_YEAR}+\n"
        f"📏 עד {MAX_KM:,} ק\"מ\n\n"
        f"⏱️ סריקה כל {CHECK_INTERVAL//60} דקות\n"
        "אשלח התראה על כל מודעה חדשה! ✅"
    )
    seen = load_seen()
    while True:
        now = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now}] מתחיל סריקה...")
        new_count = 0
        for search in SEARCHES:
            print(f"  סורק: {search['display']}")
            cars = fetch_yad2(search)
            print(f"  תוצאות: {len(cars)}")
            for car in cars:
                if car["id"] not in seen:
                    seen.add(car["id"])
                    send_alert(car)
                    new_count += 1
                    time.sleep(1.5)
        save_seen(seen)
        print(f"  התראות חדשות: {new_count}")
        print(f"  ממתין {CHECK_INTERVAL//60} דקות...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
