import requests
import json
import time
import os
import hashlib
from datetime import datetime
from bs4 import BeautifulSoup

# ─── הגדרות ───────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 3600))  # כל שעה
SEEN_FILE = "seen_ads.json"

# פילטרים
SEARCH_QUERIES = [
    {"model": "skoda-kamiq", "display": "סקודה קאמיק"},
    {"model": "skoda-karoq", "display": "סקודה קארוק"},
]

MAX_PRICE = 85000
MIN_YEAR = 2020
MAX_KM = 100000

# ─── טלגרם ────────────────────────────────────────────────
def send_telegram(message, photo_url=None):
    if photo_url:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        data = {"chat_id": CHAT_ID, "photo": photo_url, "caption": message, "parse_mode": "HTML"}
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"שגיאה בשליחה לטלגרם: {e}")

# ─── זיכרון מודעות שנראו ──────────────────────────────────
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

# ─── סריקת יד2 ────────────────────────────────────────────
def scrape_yad2(model_slug, display_name):
    results = []
    
    # URL של יד2 לפי פרמטרים
    url = (
        f"https://www.yad2.co.il/vehicles/cars"
        f"?manufacturer=skoda"
        f"&model={model_slug}"
        f"&year={MIN_YEAR}-0"
        f"&price=0-{MAX_PRICE}"
        f"&km=0-{MAX_KM}"
    )
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # חיפוש פריטים ביד2
        items = soup.find_all("div", class_=lambda c: c and "feed-item" in c)
        
        if not items:
            # ניסיון עם מבנה אחר
            items = soup.find_all("article")
        
        for item in items[:20]:  # מקסימום 20 תוצאות
            try:
                ad = parse_yad2_item(item, display_name, url)
                if ad:
                    results.append(ad)
            except Exception as e:
                print(f"שגיאה בפריסת פריט: {e}")
                
    except Exception as e:
        print(f"שגיאה בסריקת יד2 ({display_name}): {e}")
    
    return results

def parse_yad2_item(item, display_name, base_url):
    try:
        # נסה למצוא קישור
        link_tag = item.find("a", href=True)
        if not link_tag:
            return None
        
        href = link_tag["href"]
        if not href.startswith("http"):
            href = "https://www.yad2.co.il" + href
        
        # ID ייחודי מהקישור
        ad_id = hashlib.md5(href.encode()).hexdigest()[:12]
        
        # מחיר
        price_tag = item.find(class_=lambda c: c and "price" in str(c).lower())
        price = price_tag.get_text(strip=True) if price_tag else "לא צוין"
        
        # כותרת / שנה / ק"מ
        title = item.get_text(separator=" ", strip=True)[:200]
        
        return {
            "id": ad_id,
            "title": display_name,
            "price": price,
            "url": href,
            "text": title,
            "source": "יד2"
        }
    except:
        return None

# ─── API של יד2 (גישה ישירה יותר) ────────────────────────
def search_yad2_api(model_text, display_name):
    """חיפוש דרך ה-API הלא-רשמי של יד2"""
    results = []
    
    # מיפוי דגמים
    model_map = {
        "skoda-kamiq": {"manufacturer": 54, "model": 1388},  # מספרי יד2
        "skoda-karoq": {"manufacturer": 54, "model": 1389},
    }
    
    ids = model_map.get(model_text, {})
    if not ids:
        return results
    
    api_url = "https://gw.yad2.co.il/feed-search-legacy/vehicles/cars"
    params = {
        "manufacturer": ids["manufacturer"],
        "model": ids["model"],
        "yearFrom": MIN_YEAR,
        "priceTo": MAX_PRICE,
        "kmTo": MAX_KM,
        "page": 1,
        "rows": 20,
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.yad2.co.il/",
        "Accept": "application/json",
    }
    
    try:
        resp = requests.get(api_url, params=params, headers=headers, timeout=15)
        data = resp.json()
        
        items = data.get("data", {}).get("feed", {}).get("feed_items", [])
        
        for item in items:
            if item.get("type") == "ad":
                try:
                    ad_id = str(item.get("id", ""))
                    title = f"{display_name}"
                    year = item.get("year", "")
                    km = item.get("kilometers", "")
                    price = item.get("price", "")
                    city = item.get("city", "")
                    
                    # עיצוב
                    km_fmt = f"{int(km):,}" if km else "לא צוין"
                    price_fmt = f"₪{int(price):,}" if price else "לא צוין"
                    
                    photo = None
                    images = item.get("images", {})
                    if images:
                        first_img = list(images.values())[0] if isinstance(images, dict) else None
                        if first_img and isinstance(first_img, list):
                            photo = first_img[0].get("src", None)
                    
                    url = f"https://www.yad2.co.il/item/{ad_id}"
                    
                    results.append({
                        "id": ad_id,
                        "title": title,
                        "year": year,
                        "km": km_fmt,
                        "price": price_fmt,
                        "city": city,
                        "url": url,
                        "photo": photo,
                        "source": "יד2"
                    })
                except Exception as e:
                    print(f"שגיאה בפריסת מודעה: {e}")
                    
    except Exception as e:
        print(f"שגיאה ב-API של יד2 ({display_name}): {e}")
    
    return results

# ─── שליחת מודעה ──────────────────────────────────────────
def send_car_alert(car):
    emoji = "🚗"
    city_note = ""
    
    # בדיקת אזור
    city = car.get("city", "").lower()
    if any(x in city for x in ["חיפה", "קריות", "עכו", "נשר", "טירת כרמל", "זכרון"]):
        emoji = "📍🚗"
        city_note = " ← <b>קרוב אליך!</b>"
    
    message = (
        f"{emoji} <b>נמצא רכב חדש!</b>\n\n"
        f"🏷️ <b>{car['title']}</b>\n"
        f"📅 שנה: {car.get('year', 'לא צוין')}\n"
        f"📏 ק\"מ: {car.get('km', 'לא צוין')}\n"
        f"💰 מחיר: {car.get('price', 'לא צוין')}\n"
        f"📍 עיר: {car.get('city', 'לא צוין')}{city_note}\n"
        f"🔗 <a href=\"{car['url']}\">לחץ לצפייה במודעה</a>\n"
        f"\n⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    
    send_telegram(message, photo_url=car.get("photo"))

# ─── לולאה הראשית ──────────────────────────────────────────
def main():
    print(f"🚗 Car Hunter מופעל! בודק כל {CHECK_INTERVAL//60} דקות")
    send_telegram(
        "🚗 <b>Car Hunter מופעל!</b>\n\n"
        f"מחפש: סקודה קאמיק / קארוק\n"
        f"תקציב: עד ₪{MAX_PRICE:,}\n"
        f"שנה: {MIN_YEAR}+\n"
        f"ק\"מ: עד {MAX_KM:,}\n\n"
        f"אשלח לך התראה כשאמצא משהו מתאים! ✅"
    )
    
    seen = load_seen()
    
    while True:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] מתחיל סריקה...")
        new_count = 0
        
        for q in SEARCH_QUERIES:
            cars = search_yad2_api(q["model"], q["display"])
            print(f"  {q['display']}: נמצאו {len(cars)} מודעות")
            
            for car in cars:
                if car["id"] not in seen:
                    seen.add(car["id"])
                    send_car_alert(car)
                    new_count += 1
                    time.sleep(1)  # לא לשלוח הכל בבת אחת
        
        save_seen(seen)
        
        if new_count == 0:
            print("  אין מודעות חדשות")
        else:
            print(f"  נשלחו {new_count} התראות חדשות")
        
        print(f"  ממתין {CHECK_INTERVAL//60} דקות לסריקה הבאה...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
