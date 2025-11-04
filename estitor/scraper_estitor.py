#!/usr/bin/env python3
import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

import re
import time
import hashlib
import sqlite3
from datetime import datetime, timedelta
from telegram import Bot
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import html as html_lib

# --- Učitaj .env ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TARGET_URL = os.getenv("TARGET_URL")
CRAWL_INTERVAL_MINUTES = int(os.getenv("CRAWL_INTERVAL_MINUTES", 45))
MAX_PAGES = int(os.getenv("MAX_PAGES", 5))

# --- Block lista ---
CRNA_LISTA = [x.strip().lower() for x in os.getenv("CRNA_LISTA", "").split(",") if x.strip()]

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

# 🆕 Novi precizniji filter za agencije
def is_agency(seller):
    name = (seller or "").strip().lower()
    if not name:
        return True  # prazno ime = sigurno agencija

    bad_words = [
        "nekretnine", "real estate", "properties", "consulting",
        "invest", "home", "group", "estate", "realty", "luxury", "trust"
    ]
    return any(word in name for word in bad_words)

def is_private_seller(seller):
    if not seller:
        return True
    blocked_words = ["nekretnine", "real estate", "agency", "agencija"]
    return not any(bad in seller.lower() for bad in blocked_words)

# --- Čuvanje i slanje ---
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

    # --- slanje direktno putem Telegram API-ja ---
    try:
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
            print(f"⚠️ Telegram error: {response.text}")
        
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
            if pg == 1:
                url = TARGET_URL
            else:
                url = TARGET_URL.replace("/grad-podgorica", f"/grad-podgorica/strana-{pg}")

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

                # 🆕 Dodato brojanje preskočenih agencija
                skipped_agencies = 0
                sent_this_page = 0

                for o in offers:
                    if any(bad in o["seller"].lower() for bad in CRNA_LISTA):
                        skipped_agencies += 1
                        continue

                    # 🆕 Nova provjera – preskoči agencije i oglase bez imena
                    if is_agency(o["seller"]):
                        print(f"🏢 Preskačem jer je agencija ili nema ime: {o['seller']}")
                        skipped_agencies += 1
                        continue

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

                # 🆕 Novi sažetak po stranici
                print(f"✅ Stranica {pg}: {len(offers)} pronađeno, {sent_this_page} poslato, {skipped_agencies} preskočeno (agencije ili bez imena).")

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
