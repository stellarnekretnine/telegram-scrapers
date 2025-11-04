import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

import time
from datetime import datetime
from dotenv import load_dotenv
import json
from flask import Flask, request
from threading import Thread
from playwright.sync_api import sync_playwright
import requests

load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TARGET_URL = os.environ.get("TARGET_URL", "https://patuljak.me/najnoviji")
CRNA_LISTA = os.environ.get("CRNA_LISTA", "").split(",")
INTERVAL_MIN = int(os.environ.get("CRAWL_INTERVAL_MINUTES", "60"))

poslato = set()
POSLATO_FILE = "poslato.json"

# Učitaj već poslate oglase ako fajl postoji
if os.path.exists(POSLATO_FILE):
    with open(POSLATO_FILE, "r", encoding="utf-8") as f:
        try:
            poslato = set(json.load(f))
        except Exception:
            poslato = set()
else:
    poslato = set()

def sacuvaj_poslato():
    with open(POSLATO_FILE, "w", encoding="utf-8") as f:
        json.dump(list(poslato), f, ensure_ascii=False, indent=2)


# Učitaj crnu listu iz .env fajla
CRNA_LISTA = [b.strip() for b in os.environ.get("CRNA_LISTA", "").split(",") if b.strip()]

print("✅ Učitana crna lista:", CRNA_LISTA)



def send_telegram(title, url, image_url):
    if not image_url:
        text = f"<b>{title}</b>\n{url}"
        api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(api, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode":"HTML"})
    else:
        api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        try:
            img = requests.get(image_url, timeout=10).content
            requests.post(api, data={"chat_id": TELEGRAM_CHAT_ID,
                                     "caption": f"<b>{title}</b>\n{url}",
                                     "parse_mode":"HTML"},
                                     files={"photo": img})
        except Exception as e:
            print(f"[{datetime.now()}] Neuspjelo slanje slike: {e}")

def scrape():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(TARGET_URL, timeout=120000)
        time.sleep(5)
        page.wait_for_load_state("networkidle")

        # Skroluj da se učitaju svi oglasi
        for _ in range(10):
            page.mouse.wheel(0, 2000)
            time.sleep(5)

        elements = page.query_selector_all("div[class*='product__v']")
        elements = elements[60:]  # preskače prvih 40 (promovisane oglase)


        for el in elements:
            try:
                # --- naslov i link ---
                title_el = el.query_selector("a")
                if not title_el:
                    continue
                title = title_el.inner_text().strip()
                url = title_el.get_attribute("href")
                if url and url.startswith("/"):
                    url = "https://patuljak.me" + url

                # --- slika ---
                img = el.query_selector("img")
                image_url = img.get_attribute("src") if img else None
                if image_url and image_url.startswith("/"):
                    image_url = "https://patuljak.me" + image_url

                # --- otvori stranicu oglasa da pokupimo ime prodavca ---
                seller_name = ""
                if url:
                    try:
                        page2 = browser.new_page()
                        page2.goto(url, timeout=30000)
                        page2.wait_for_timeout(500)  # sačekaj 2 sekunde da se sve učita
                        seller_el = page2.query_selector("h6 a")
                        if seller_el:
                            seller_name = seller_el.inner_text().strip()
                        page2.close()
                    except Exception as e:
                        print(f"Greška pri čitanju prodavca: {e}")

                print("Prodavac:", seller_name)

                # --- dodaj rezultat ---
                results.append({
                    "title": title,
                    "url": url,
                    "image_url": image_url,
                    "seller": seller_name
                })

            except Exception as e:
                print(f"Greška pri čitanju oglasa: {e}")
                continue

         # Ukloni duplikate po URL-u
        unique_results = []
        seen_urls = set()
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                unique_results.append(r)
        results = unique_results


        browser.close()
    return results

def blocked_by_blacklist(seller_name: str) -> bool:
    """Vrati True samo ako se ime tačno poklapa s nekom agencijom iz CRNA_LISTA (case-insensitive)."""
    s = seller_name.strip().lower()
    for b in CRNA_LISTA:
        if not b:
            continue
        if s == b.strip().lower():
            print(f"⛔ Blokiran prodavac: {seller_name}")
            return True
    return False

def main_loop():
    print("Pokrećem Playwright scraper...")
    while True:
        try:
            listings = scrape()
            print(f"[{datetime.now()}] Pronađeno {len(listings)} oglasa")
            for oglas in listings:
                if oglas["url"] in poslato:
                    continue
                if blocked_by_blacklist(oglas["seller"]):
                    print(f"Preskačem: {oglas['seller']}")
                    continue

                send_telegram(oglas["title"], oglas["url"], oglas["image_url"])
                poslato.add(oglas["url"])
                sacuvaj_poslato()

        except Exception as e:
            print(f"[{datetime.now()}] Greška: {e}")
        time.sleep(INTERVAL_MIN * 60)

app = Flask(__name__)

@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data or "message" not in data:
        return {"ok": True}

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "")

    if text.lower().startswith("/block "):
        ime = text[7:].strip()
        if ime and ime not in CRNA_LISTA:
            CRNA_LISTA.append(ime)
            sacuvaj_crnu_listu()
            msg = f"✅ Dodan '{ime}' u crnu listu!"
        else:
            msg = f"ℹ️ '{ime}' je već na crnoj listi."
        api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(api, data={"chat_id": chat_id, "text": msg})
    elif text.lower() == "/lista":
        msg = "📋 CRNA LISTA:\n" + "\n".join(f"- {n}" for n in CRNA_LISTA)
        api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(api, data={"chat_id": chat_id, "text": msg})
    return {"ok": True}

if __name__ == "__main__":
    print("Pokrećem glavni scraper bez Flask servera...")
    try:
        main_loop()
    except KeyboardInterrupt:
        print("Zaustavljeno ručno.")
