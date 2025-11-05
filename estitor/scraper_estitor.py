import os
import re
import time
import hashlib
import sqlite3
import unicodedata
from datetime import datetime
from telegram import Bot
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import subprocess
import html as html_lib
import requests

# Instalacija Chromium-a (bez greške ako je već instaliran)
subprocess.run(["python", "-m", "playwright", "install", "chromium"], check=False)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TARGET_URL = os.getenv("TARGET_URL")
CRAWL_INTERVAL_MINUTES = int(os.getenv("CRAWL_INTERVAL_MINUTES", 45))
MAX_PAGES = int(os.getenv("MAX_PAGES", 5))

# --- Crna lista direktno u kodu ---
CRNA_LISTA = [
    "nova lux invest realestate",
    "damir jukovic",
    "nemanja krstović",
    "nemanja bulajić",
    "dandelion agencija",
    "milica mitrović",
    "dream homes montenegro",
    "luxury property",
    "marija panoska",
    "roma nekretnine",
    "kvadrat nekretnine",
    "valentina stanišić",
    "sara panoska",
    "menalex real state",
    "violet investment",
    "master realestate",
    "puerta real estate",
    "mat nekretnine nekretnine",
    "lumaro properties",
    "forum nekretnine",
    "diem nekretnine",
    "dm real estate",
    "living real estate",
    "in nekretnine",
    "mne real estate",
    "blok nekretnine podgorica",
    "centar nekretnine",
    "jutro nekretnine",
    "multitask nekretnine",
    "remontenegro - real estate montenegro",
    "kvart real estate",
    "domus nekretnine",
    "nekretnine menadžer",
    "milena tajić",
    "alda realty group",
    "dragana z",
    "focus nekretnine",
    "city_properties_nekretnine",
    "taluma montenegro",
    "prego nekretnine",
    "pg nekretnine",
    "agencija manzil home",
    "ismail dacic",
    "nikola lekić",
    "jelena marković",
    "vedad kurpejović",
    "jb global asset",
    "nikola ivanovic",
    "marija ivanovic",
    "dimitrije bozovic",
    "sladja lazarević",
]
print(f"✅ Učitano {len(CRNA_LISTA)} imena iz crne liste (direktno iz koda).")

# --- Normalizacija imena ---
def normalize_name(name):
    """Normalizuje ime (uklanja čćžšđ, velika slova, duple razmake)."""
    if not name:
        return ""
    name = name.strip().lower()
    name = " ".join(name.split())
    name = unicodedata.normalize("NFD", name)
    name = "".join(ch for ch in name if unicodedata.category(ch) != "Mn")
    name = (
        name.replace("đ", "dj")
            .replace("dž", "dz")
            .replace("č", "c")
            .replace("ć", "c")
            .replace("š", "s")
            .replace("ž", "z")
    )
    return name

CRNA_LISTA = [normalize_name(x) for x in CRNA_LISTA]

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

def is_agency(seller):
    name = (seller or "").strip().lower()
    if not name:
        return True
    bad_words = [
        "nekretnine", "real estate", "properties", "consulting",
        "invest", "home", "group", "estate", "realty", "luxury", "trust"
    ]
    return any(word in name for word in bad_words)

def store_and_notify(item):
    uid = make_id(item["url"])
    c.execute("SELECT 1 FROM listings WHERE id=?", (uid,))
    if c.fetchone():
        return False

    caption = (
        f"🏠 <b>{item['title']}</b>\n"
        f"💶 {item['price']}\n"
        f"📍 {item['location']}\n"
        f"👤 {item['seller']}\n\n"
        f"<a href='{item['url']}'>🔗 Pogledaj oglas</a>"
    )[:1000]

    c.execute("""INSERT INTO listings (id, title, price, location, url, img_url, seller, first_seen)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (uid, item["title"], item["price"], item["location"],
               item["url"], item["img_url"], item["seller"],
               datetime.now().astimezone().isoformat()))
    conn.commit()

    try:
        if item["img_url"]:
            api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            payload = {"chat_id": TG_CHAT_ID, "photo": item["img_url"], "caption": caption, "parse_mode": "HTML"}
        else:
            api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": TG_CHAT_ID, "text": caption, "parse_mode": "HTML"}
        response = requests.post(api_url, data=payload)
        if response.status_code == 200:
            print(f"📤 Poslato: {item['title']}")
        else:
            print(f"⚠️ Telegram error: {response.text}")
        time.sleep(1)
    except Exception as e:
        print("⚠️ Greška pri slanju poruke:", e)
    return True

def parse_offers(html):
    offers = []
    raw_blocks = re.findall(r'\{"@type":"Offer".*?\}\}', html, re.DOTALL)
    for block in raw_blocks:
        try:
            block = html_lib.unescape(block)
            title = re.search(r'"name":"(.*?)"', block)
            price = re.search(r'"price":"(\d+)"', block)
            url = re.search(r'"url":"(https:[^"]+)"', block)
            loc = re.search(r'"addressLocality":"(.*?)"', block)
            img = re.search(r'"image":\{"@type":"ImageObject","url":"(https:[^"]+)"', block)
            seller = re.search(r'"seller".*?"name":"(.*?)"', block)
            offers.append({
                "title": title.group(1) if title else "Nekretnina",
                "price": f"{price.group(1)} €" if price else "Po dogovoru",
                "location": loc.group(1) if loc else "Podgorica",
                "url": url.group(1).replace("\\/", "/") if url else "",
                "img_url": img.group(1).replace("\\/", "/") if img else "",
                "seller": seller.group(1) if seller else "",
            })
        except Exception:
            continue
    return offers

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
                    seller_clean = normalize_name(o.get("seller"))
                    # Provjera crne liste
                    if any(bad in seller_clean for bad in CRNA_LISTA):
                        print(f"⛔ Preskačem oglas jer je na crnoj listi: {o['seller']}")
                        skipped_agencies += 1
                        continue
                    # Preskoči agencije
                    if is_agency(o["seller"]):
                        print(f"🏢 Preskačem jer je agencija ili nema ime: {o['seller']}")
                        skipped_agencies += 1
                        continue
                    # Ako je OK, pošalji
                    if store_and_notify(o):
                        total_new += 1
                        sent_this_page += 1

                print(f"✅ Stranica {pg}: {len(offers)} pronađeno, {sent_this_page} poslato, {skipped_agencies} preskočeno.")
            except Exception as e:
                print(f"⚠️ Greška na strani {pg}: {e}")

        browser.close()
        print(f"📊 Ukupno novih oglasa: {total_new}")

if __name__ == "__main__":
    while True:
        print("🔎 Pokrećem provjeru Estitor oglasa...")
        scrape_with_playwright()
        print(f"💤 Čekam {CRAWL_INTERVAL_MINUTES} minuta prije sljedeće provjere...\n")
        time.sleep(CRAWL_INTERVAL_MINUTES * 60)
