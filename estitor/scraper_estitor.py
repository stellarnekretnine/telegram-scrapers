import os
import re
import time
import hashlib
import sqlite3
from datetime import datetime, timedelta
from telegram import Bot
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import subprocess
import html as html_lib

# Instalacija Chromium-a (samo ako nije već instaliran)
subprocess.run(["python", "-m", "playwright", "install", "chromium"], check=False)

# --- Učitaj .env ---
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TARGET_URL = os.getenv("TARGET_URL")
CRAWL_INTERVAL_MINUTES = int(os.getenv("CRAWL_INTERVAL_MINUTES", 45))
MAX_PAGES = int(os.getenv("MAX_PAGES", 5))

# --- Block lista ---
try:
    crna_lista_path = os.getenv("CRNA_LISTA_FILE", "/etc/secrets/crna_lista.txt")

    # Sačekaj da Render mountuje fajl (nekad kasni sekund-dva)
    for _ in range(5):
        if os.path.exists(crna_lista_path):
            break
        print("⌛ Čekam da Render učita crna_lista.txt...")
        time.sleep(2)

    if os.path.exists(crna_lista_path):
        with open(crna_lista_path, "r", encoding="utf-8") as f:
            CRNA_LISTA = [line.strip().lower() for line in f if line.strip()]
        print(f"✅ Učitano {len(CRNA_LISTA)} imena iz crne liste.")
    else:
        print("⚠️ Nije pronađen fajl crna_lista.txt — crna lista prazna.")
        CRNA_LISTA = []

except Exception as e:
    print(f"⚠️ Greška prilikom učitavanja crne liste: {e}")
    CRNA_LISTA = []

# --- Setup ---
DB_PATH = "estitor.db"
bot = Bot(token=TELEGRAM_TOKEN)

# --- Baza ---
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    title TEXT,
    price TEXT,
    location TEXT,
    url TEXT,
    img_url TEXT,
    seller TEXT,
    first_seen TEXT
)""")
conn.commit()

def make_id(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()

# --- Prepoznavanje agencija ---
def is_agency(seller):
    name = (seller or "").strip().lower()
    if not name:
        return True  # prazno ime = vjerovatno agencija

    bad_words = [
        "nekretnine", "real estate", "properties", "consulting",
        "invest", "home", "group", "estate", "realty", "luxury", "trust"
    ]
    return any(word in name for word in bad_words)

# --- Čuvanje i slanje oglasa ---
def store_and_notify(item):
    import requests

    uid = make_id(item["url"])
    c.execute("SELECT 1 FROM listings WHERE id=?", (uid,))
    if c.fetchone():
        return False  # već postoji

    caption = (
        f"🏠 <b>{item['title']}</b>\n"
        f"💶 {item['price']}\n"
        f"📍 {item['location']}\n"
        f"👤 {item['seller']}\n\n"
        f"<a href='{item['url']}'>🔗 Pogledaj oglas</a>"
    )
    caption = caption[:1000]

    c.execute("""INSERT INTO listings (id, title, price, location, url, img_url, seller, first_seen)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (uid, item["title"], item["price"], item["location"],
               item["url"], item["img_url"], item["seller"],
               datetime.now().astimezone().isoformat()))
    conn.commit()

    try:
        api_url = ""
        payload = {}

        if item["img_url"]:
            api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TG_CHAT_ID,
                "photo": item["img_url"],
                "caption": caption,
                "parse_mode": "HTML"
            }
        else:
            api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {
                "chat_id": TG_CHAT_ID,
                "text": caption,
                "parse_mode": "HTML"
            }

        response = requests.post(api_url, data=payload)
        if response.status_code == 200:
            print(f"📤 Poslato: {item['title']}")
        else:
            print(f"⚠️ Telegram greška: {response.text}")
        
        time.sleep(1)
    except Exception as e:
        print("⚠️ Greška pri slanju poruke:", e)

    return True

# --- Parser ---
def parse_offers(html):
    offers = []
    raw_blocks = re.findall(r'\{"@type":"Offer".*?\}\}', html, re.DOTALL)
    for block in raw_blocks:
        try:
            block = html_lib.unescape(block)
            title_match = re.search(r'"name":"(.*?)"', block)
            price_match = re.search(r'"price":"(\d+)"', block)
            url_match = re.search(r'"url":"(https:[^"]+)"', block)
            loc_match = re.search(r'"addressLocality":"(.*?)"', block)
            img_match = re.search(r'"image":\{"@type":"ImageObject","url":"(https:[^"]+)"', block)
            seller_match = re.search(r'"seller".*?"name":"(.*?)"', block)
            time_match = re.search(r'"datePublished":"(.*?)"', block)

            title = title_match.group(1) if title_match else "Nekretnina"
            price = f"{price_match.group(1)} €" if price_match else "Po dogovoru"
            url = url_match.group(1).replace("\\/", "/") if url_match else ""
            location = loc_match.group(1) if loc_match else "Podgorica"
            img_url = img_match.group(1).replace("\\/", "/") if img_match else ""
            seller = seller_match.group(1) if seller_match else ""
            published = time_match.group(1) if time_match else ""

            offers.append({
                "title": title,
                "price": price,
                "location": location,
                "url": url,
                "img_url": img_url,
                "seller": seller,
                "published": published
            })
        except Exception:
            continue
    return offers

# --- Main scraping ---
def scrape_with_playwright():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        total_new = 0

        for pg in range(1, MAX_PAGES + 1):
            url = TARGET_URL if pg == 1 else TARGET_URL.replace("/grad-podgorica", f"/grad-podgorica/strana-{pg}")
            print(f"📄 Stranica {pg}: {url}")

            try:
                page.goto(url, timeout=90000)
                page.wait_for_load_state("networkidle")
                time.sleep(5)

                for _ in range(10):
                    page.mouse.wheel(0, 2500)
                    time.sleep(2)

                html = page.content()
                offers = parse_offers(html)
                print(f"🔎 Pronađeno blokova: {len(offers)}")

                skipped_agencies = 0
                sent_this_page = 0

                for o in offers:
                    seller_name = (o.get("seller") or "").strip().lower()

                    # Normalizacija karaktera
                    for src, dst in [("č", "c"), ("ć", "c"), ("š", "s"), ("ž", "z"), ("đ", "dj")]:
                        seller_name = seller_name.replace(src, dst)

                    crna_lista_normalized = []
                    for bad in CRNA_LISTA:
                        bad_norm = bad.strip().lower()
                        for src, dst in [("č", "c"), ("ć", "c"), ("š", "s"), ("ž", "z"), ("đ", "dj")]:
                            bad_norm = bad_norm.replace(src, dst)
                        crna_lista_normalized.append(bad_norm)

                    # Provjera crne liste
                    if any(bad in seller_name for bad in crna_lista_normalized):
                        print(f"⛔ Preskačem oglas jer je na crnoj listi: {o['seller']}")
                        skipped_agencies += 1
                        continue

                    # Preskoči ako je agencija
                    if is_agency(o["seller"]):
                        print(f"🏢 Preskačem jer je agencija ili nema ime: {o['seller']}")
                        skipped_agencies += 1
                        continue

                    # Ako je sve OK — pošalji oglas
                    item = {
                        "title": o["title"],
                        "price": o["price"],
                        "location": o["location"],
                        "url": o["url"],
                        "img_url": o["img_url"],
                        "seller": o["seller"]
                    }

                    if store_and_notify(item):
                        total_new += 1
                        sent_this_page += 1

                print(f"✅ Stranica {pg}: {len(offers)} pronađeno, {sent_this_page} poslato, {skipped_agencies} preskočeno.")

            except Exception as e:
                print(f"⚠️ Greška na strani {pg}: {e}")

        browser.close()
        print(f"📊 Ukupno novih oglasa: {total_new}")

# --- Glavna petlja ---
if __name__ == "__main__":
    while True:
        print("🔎 Pokrećem provjeru Estitor oglasa...")
        scrape_with_playwright()
        print(f"💤 Čekam {CRAWL_INTERVAL_MINUTES} minuta prije sljedeće provjere...\n")
        time.sleep(CRAWL_INTERVAL_MINUTES * 60)
