"""
Car Hunter — yad2.co.il scraper using Playwright (headless Chromium).

Rewritten to defeat Railway's bot detection against gw.yad2.co.il by driving
a real Chromium browser session, establishing cookies on yad2.co.il first,
and then extracting results from either the Next.js embedded JSON
(__NEXT_DATA__) or intercepted XHR/fetch responses from the internal API.
"""

import asyncio
import json
import os
import hashlib
from datetime import datetime

import requests
from playwright.async_api import async_playwright

# ─── הגדרות ───────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 3600))  # כל שעה
SEEN_FILE = os.environ.get("SEEN_FILE", "seen_ads.json")

# פילטרים
MAX_PRICE = 85000
MIN_YEAR = 2020
MAX_KM = 100000

# manufacturer=40 = Skoda on yad2's internal IDs
SEARCH_QUERIES = [
    {
        "display": "סקודה קאמיק",
        "manufacturer": 40,
        "model_id": 10544,
    },
    {
        "display": "סקודה קארוק",
        "manufacturer": 40,
        "model_id": 10545,
    },
]

# אזור חיפה / זכרון יעקב
HAIFA_AREA_KEYWORDS = [
    "חיפה", "קריות", "קרית", "עכו", "נשר", "טירת כרמל",
    "זכרון", "זיכרון", "יוקנעם", "יקנעם", "עתלית", "טבעון",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


# ─── טלגרם ────────────────────────────────────────────────
def send_telegram(message: str, photo_url: str | None = None) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ TELEGRAM_TOKEN / CHAT_ID not set — skipping send")
        return

    try:
        if photo_url:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            data = {
                "chat_id": CHAT_ID,
                "photo": photo_url,
                "caption": message,
                "parse_mode": "HTML",
            }
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            data = {
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
        resp = requests.post(url, data=data, timeout=15)
        if resp.status_code != 200:
            # If photo fails (bad URL, etc.), fall back to text only.
            if photo_url:
                print(f"Telegram photo failed ({resp.status_code}); retrying as text")
                send_telegram(message, photo_url=None)
            else:
                print(f"Telegram error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"שגיאה בשליחה לטלגרם: {e}")


# ─── זיכרון מודעות שנראו ──────────────────────────────────
def load_seen() -> set[str]:
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"load_seen error: {e}")
    return set()


def save_seen(seen: set[str]) -> None:
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen), f, ensure_ascii=False)
    except Exception as e:
        print(f"save_seen error: {e}")


# ─── חילוץ פריטים מ-JSON ──────────────────────────────────
def _walk_collect(node, keys: tuple[str, ...], out: list) -> None:
    """Walk arbitrary JSON and collect all lists found under any of `keys`."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys and isinstance(v, list):
                for it in v:
                    if isinstance(it, dict):
                        out.append(it)
            _walk_collect(v, keys, out)
    elif isinstance(node, list):
        for v in node:
            _walk_collect(v, keys, out)


def extract_feed_items(payload) -> list[dict]:
    """
    Dig through yad2's response shapes to pull out actual listings.
    Handles both the legacy feed API and the Next.js embedded props.
    """
    buckets: list[dict] = []
    _walk_collect(
        payload,
        ("feed_items", "commercial_items", "private_items", "items", "solo_items"),
        buckets,
    )

    # De-duplicate by any id-like field
    seen_ids: set[str] = set()
    unique: list[dict] = []
    for it in buckets:
        iid = str(
            it.get("id")
            or it.get("ad_number")
            or it.get("token")
            or it.get("orderId")
            or hashlib.md5(
                json.dumps(it, sort_keys=True, default=str).encode()
            ).hexdigest()
        )
        if iid in seen_ids:
            continue
        seen_ids.add(iid)
        unique.append(it)
    return unique


def parse_item(item: dict, display_name: str) -> dict | None:
    """Normalize one raw yad2 listing dict into our alert schema."""
    try:
        item_type = (item.get("type") or item.get("ad_type") or "").lower()
        # Skip non-listings (banners, ads-between-ads, etc.)
        if item_type and item_type not in ("ad", "private", "commercial", "listing"):
            return None

        ad_id = str(
            item.get("id")
            or item.get("ad_number")
            or item.get("token")
            or ""
        ).strip()
        if not ad_id:
            return None

        year = item.get("year") or item.get("yearOfProduction") or ""
        km = item.get("kilometers") or item.get("km") or ""
        price = item.get("price") or item.get("priceValue") or ""

        city = (
            item.get("city")
            or item.get("city_text")
            or item.get("address_area")
            or item.get("area")
            or item.get("row_1")
            or ""
        )
        if isinstance(city, dict):
            city = city.get("text") or city.get("title") or ""

        # Format numbers
        try:
            km_fmt = f"{int(km):,}" if km not in (None, "", 0) else "לא צוין"
        except Exception:
            km_fmt = str(km) if km else "לא צוין"

        try:
            price_fmt = f"₪{int(price):,}"
        except Exception:
            price_fmt = "לא צוין"

        # Budget / filter guardrail (yad2 sometimes returns out-of-range items)
        try:
            if int(price) > MAX_PRICE * 1.05:
                return None
        except Exception:
            pass

        # Image URL
        photo = None
        images = item.get("images") or item.get("image_urls") or item.get("media")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, str):
                photo = first
            elif isinstance(first, dict):
                photo = first.get("src") or first.get("url") or first.get("uri")
        elif isinstance(images, dict):
            for v in images.values():
                if isinstance(v, list) and v:
                    candidate = v[0]
                    if isinstance(candidate, dict):
                        photo = candidate.get("src") or candidate.get("url")
                    elif isinstance(candidate, str):
                        photo = candidate
                    break
                if isinstance(v, str):
                    photo = v
                    break

        token = item.get("token") or ad_id
        url = f"https://www.yad2.co.il/item/{token}"

        return {
            "id": ad_id,
            "title": display_name,
            "year": year,
            "km": km_fmt,
            "price": price_fmt,
            "city": str(city) if city else "",
            "url": url,
            "photo": photo,
            "source": "יד2",
        }
    except Exception as e:
        print(f"parse_item error: {e}")
        return None


# ─── סריקה באמצעות Playwright ────────────────────────────
async def scrape_yad2(playwright, query: dict) -> list[dict]:
    """
    Launches headless Chromium, warms up with yad2.co.il homepage to get cookies,
    then navigates to the search URL and harvests listings from both the
    embedded __NEXT_DATA__ and any intercepted API XHRs.
    """
    manufacturer = query["manufacturer"]
    model_id = query["model_id"]
    display_name = query["display"]

    search_url = (
        "https://www.yad2.co.il/vehicles/cars"
        f"?manufacturer={manufacturer}"
        f"&model={model_id}"
        f"&year={MIN_YEAR}--1"
        f"&price=-1-{MAX_PRICE}"
        f"&km=-1-{MAX_KM}"
    )

    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )

    context = await browser.new_context(
        user_agent=USER_AGENT,
        locale="he-IL",
        timezone_id="Asia/Jerusalem",
        viewport={"width": 1366, "height": 768},
        extra_http_headers={
            "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
        },
    )

    # Mild stealth — hide webdriver flag so common anti-bot checks pass.
    await context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['he-IL','he','en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
        window.chrome = { runtime: {} };
        """
    )

    page = await context.new_page()

    # Capture any JSON responses from yad2 API calls as a fallback.
    captured: list[dict] = []

    async def on_response(response):
        try:
            url = response.url
            if "yad2.co.il" not in url:
                return
            ctype = response.headers.get("content-type", "")
            if "json" not in ctype.lower():
                return
            # Only interesting API endpoints
            if not any(
                part in url
                for part in ("gw.yad2", "/api/", "feed-search", "vehicles/cars")
            ):
                return
            body = await response.json()
            captured.append(body)
        except Exception:
            # Some responses can't be read (e.g. navigations) — ignore.
            pass

    page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

    results: list[dict] = []

    try:
        # Warm up: establish session + cookies on main vehicles landing page.
        print(f"    → warming up on yad2.co.il/vehicles/cars")
        await page.goto(
            "https://www.yad2.co.il/vehicles/cars",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        await page.wait_for_timeout(2500)

        # Now navigate to the filtered search URL.
        print(f"    → navigating to {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

        # Give SPA time to hydrate + fire XHRs
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(4000)

        # Lazy-load: gentle scroll to trigger any virtualized feed renders
        try:
            await page.evaluate(
                "window.scrollTo(0, document.body.scrollHeight * 0.6)"
            )
            await page.wait_for_timeout(1500)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(500)
        except Exception:
            pass

        # 1) Try to pull listings out of __NEXT_DATA__
        try:
            next_json = await page.evaluate(
                """
                () => {
                    const el = document.getElementById('__NEXT_DATA__');
                    return el ? el.textContent : null;
                }
                """
            )
            if next_json:
                data = json.loads(next_json)
                items = extract_feed_items(data)
                print(f"    → __NEXT_DATA__ yielded {len(items)} raw items")
                for it in items:
                    parsed = parse_item(it, display_name)
                    if parsed:
                        results.append(parsed)
        except Exception as e:
            print(f"    ! __NEXT_DATA__ parse error: {e}")

        # 2) Fallback: harvest whatever we captured from API XHRs
        if not results and captured:
            print(f"    → trying {len(captured)} captured JSON responses")
            for body in captured:
                items = extract_feed_items(body)
                for it in items:
                    parsed = parse_item(it, display_name)
                    if parsed:
                        results.append(parsed)

        if not results:
            print(f"    ! no listings found for {display_name}")

    except Exception as e:
        print(f"scrape_yad2 error ({display_name}): {e}")
    finally:
        await context.close()
        await browser.close()

    # De-duplicate final results by ad id
    seen_ids: set[str] = set()
    deduped: list[dict] = []
    for r in results:
        if r["id"] in seen_ids:
            continue
        seen_ids.add(r["id"])
        deduped.append(r)
    return deduped


# ─── שליחת מודעה ──────────────────────────────────────────
def send_car_alert(car: dict) -> None:
    emoji = "🚗"
    city_note = ""

    city_str = str(car.get("city") or "")
    if any(x in city_str for x in HAIFA_AREA_KEYWORDS):
        emoji = "📍🚗"
        city_note = " ← <b>קרוב אליך!</b>"

    message = (
        f"{emoji} <b>נמצא רכב חדש!</b>\n\n"
        f"🏷️ <b>{car['title']}</b>\n"
        f"📅 שנה: {car.get('year', 'לא צוין')}\n"
        f"📏 ק\"מ: {car.get('km', 'לא צוין')}\n"
        f"💰 מחיר: {car.get('price', 'לא צוין')}\n"
        f"📍 עיר: {car.get('city') or 'לא צוין'}{city_note}\n"
        f"🔗 <a href=\"{car['url']}\">לחץ לצפייה במודעה</a>\n"
        f"\n⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    send_telegram(message, photo_url=car.get("photo"))


# ─── לולאה ראשית ──────────────────────────────────────────
async def scan_once(seen: set[str]) -> int:
    new_count = 0
    async with async_playwright() as p:
        for q in SEARCH_QUERIES:
            print(f"  {q['display']}: starting scrape")
            try:
                cars = await scrape_yad2(p, q)
            except Exception as e:
                print(f"  {q['display']}: scrape failed: {e}")
                cars = []
            print(f"  {q['display']}: found {len(cars)} listings")

            for car in cars:
                if car["id"] in seen:
                    continue
                seen.add(car["id"])
                send_car_alert(car)
                new_count += 1
                await asyncio.sleep(1)  # rate-limit Telegram

            # Small delay between queries
            await asyncio.sleep(2)
    return new_count


async def main() -> None:
    print(f"🚗 Car Hunter מופעל! בודק כל {CHECK_INTERVAL // 60} דקות")

    send_telegram(
        "🚗 <b>Car Hunter מופעל!</b>\n\n"
        f"מחפש: סקודה קאמיק / קארוק\n"
        f"תקציב: עד ₪{MAX_PRICE:,}\n"
        f"שנה: {MIN_YEAR}+\n"
        f"ק\"מ: עד {MAX_KM:,}\n\n"
        "אשלח לך התראה כשאמצא משהו מתאים! ✅"
    )

    seen = load_seen()
    print(f"   (loaded {len(seen)} previously-seen ad ids)")

    while True:
        stamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{stamp}] מתחיל סריקה...")
        try:
            new_count = await scan_once(seen)
            save_seen(seen)
            if new_count == 0:
                print("  אין מודעות חדשות")
            else:
                print(f"  נשלחו {new_count} התראות חדשות")
        except Exception as e:
            print(f"  שגיאה בסריקה: {e}")
            send_telegram(f"⚠️ שגיאה בסריקה: {str(e)[:300]}")

        print(f"  ממתין {CHECK_INTERVAL // 60} דקות לסריקה הבאה...")
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
