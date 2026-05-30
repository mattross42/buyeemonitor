import re
import os
import time
import asyncio
import imaplib
import email
from datetime import datetime
import requests
import feedparser
import xml.etree.ElementTree as ET
from deep_translator import GoogleTranslator
from supabase import create_client, Client
from mercapi import Mercapi
from mercapi.requests import SearchRequestData
from urllib.parse import quote

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
DISCORD_WEBHOOK_EBAY = os.getenv("DISCORD_WEBHOOK_EBAY")

# Plain keywords now (NOT URLs). Japanese or English both work. Edit freely.
PIN_CATEGORY = 975  # キャラクターグッズ → ピンズ・ピンバッジ・缶バッジ; applied to every search
EXCLUDE_WORDS = "twisted ツイステッド ツイステ 缶バッジ"

# Plain keywords. Each is auto-filtered to the pin/badge category above.
# Cut any lines you don't collect — fewer terms = faster, lighter runs.
SEARCH_TERMS = [
    # --- Disney umbrella ---
    "ディズニー", "ディズニーランド", "disney",
    # --- Mickey & friends ---
    "ミッキー",
    # --- Winnie the Pooh ---
    "くまのプーさん", "イーヨー",
    # --- Princesses & their films ---
    "眠れる森の美女", "リトルマーメイド",
    "美女と野獣", "ラプンツェル",
    "モアナ", "ティアナ",
    # --- Classics & other films ---
    "ふしぎの国のアリス",
    "ライオンキング", "ヘラクレス", "ノートルダムの鐘",
    # --- Villains ---
    "マレフィセント", "クルエラ",
    # --- Nightmare Before Christmas ---
    "ナイトメアビフォアクリスマス",
    # --- Stitch / Lilo & Stitch ---
    "スティッチ", "リロ&スティッチ",
    # --- Pixar ---
    "ピクサー", "トイストーリー",
    "モンスターズインク", "pixar",
    "レミーのおいしいレストラン", "カールじいさんの空飛ぶ家",
    "インサイドヘッド",
    "私ときどきレッサーパンダ", "ベイマックス", "ズートピア",
    # --- Star Wars (both spellings) ---
    "スターウォーズ", "スター・ウォーズ", "STAR WARS",
    "グローグー", "マンダロリアン",
]

EBAY_SEARCH_TERMS = [
    ("999 happy haunts pin", None),
    ("Disney pin frame", 100),
    ("Disney pin gomes", None),
    ("Disney pin rare", 499),
    ("Disney profile pin wdi", None),
    ("Micmo523584", None),
    ("Stilladddear", None),
    ('Stitch "Disney auctions" pin', None),
    ('Stitch "Disney shopping" pin', None),
    ("Sunny_days_ahead_shop", None),
    ('"global security" Disney pin', None),
]

LIMIT_PER_SEARCH = 50  # newest N kept per search; higher = deeper back-catalog on first run

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
m = Mercapi()

async def scrape_term(term):
    print(f"Searching: {term}")
    try:
       results = await m.search(
            term,
            categories=[PIN_CATEGORY],
            exclude=EXCLUDE_WORDS,
            sort_by=SearchRequestData.SortBy.SORT_CREATED_TIME,
            sort_order=SearchRequestData.SortOrder.ORDER_DESC,
            status=[SearchRequestData.Status.STATUS_ON_SALE],
        )
    except Exception as e:
        print(f"  search error: {e}")
        return []
    items = results.items[:LIMIT_PER_SEARCH]
    print(f"  -> {len(items)} items (of {results.meta.num_found} total)")
    await asyncio.sleep(0.7)   # be gentle on Mercari's API
    if items:
        first = items[0]
        print("DEBUG fields:", {
            "id_": getattr(first, "id_", "MISSING"),
            "id": getattr(first, "id", "MISSING"),
            "name": getattr(first, "name", "MISSING"),
            "price": getattr(first, "price", "MISSING"),
            "thumbnails": getattr(first, "thumbnails", "MISSING"),
        })
    return items

def dedupe_and_store(items, term):
    new_items = []
    for it in items:
        try:
            if isinstance(it, dict):
                # Already-built dict (eBay/Flippah)
                item_url = it.get("item_url")
                if not item_url:
                    continue
                item_data = {
                    "id": item_url,
                    "item_url": item_url,
                    "title": it.get("title"),
                    "price": it.get("price"),
                    "seller": it.get("seller", "eBay"),
                    "condition": it.get("condition", "N/A"),
                    "search_term": it.get("search_term", term),
                    "image_url": it.get("image_url"),
                    "last_seen": datetime.now().isoformat(),
                }
            else:
                # Mercari object
                item_id = getattr(it, "id_", None) or getattr(it, "id", None)
                if not item_id:
                    continue
                item_url = f"https://buyee.jp/mercari/item/{item_id}"
                thumbs = getattr(it, "thumbnails", None) or []
                item_data = {
                    "id": str(item_id),
                    "item_url": item_url,
                    "title": getattr(it, "name", None),
                    "price": getattr(it, "price", None),
                    "seller": "Mercari",
                    "condition": "N/A",
                    "search_term": term,
                    "image_url": thumbs[0] if thumbs else None,
                    "last_seen": datetime.now().isoformat(),
                }

            existing = supabase.table("buyee_items").select("item_url").eq(
                "item_url", item_url
            ).execute()
            if existing.data:
                supabase.table("buyee_items").update({
                    "last_seen": datetime.now().isoformat(),
                    "is_new": False,
                }).eq("item_url", item_url).execute()
            else:
                supabase.table("buyee_items").insert(item_data).execute()
                new_items.append(item_data)
                print(f"  NEW: {item_data['title']} - {item_data['price']}")
        except Exception as e:
            print(f"  store error: {e}")
            continue
    return new_items

def translate_title(text):
    if not text:
        return text
    try:
        return GoogleTranslator(source="ja", target="en").translate(text)
    except Exception as e:
        print(f"  translate error: {e}")
        return text  # fall back to the original on any hiccup
        
def send_discord_alert(items, webhook=None):
    if not webhook:
        webhook = DISCORD_WEBHOOK
    if not items or not DISCORD_WEBHOOK:
        return
    # Discord allows max 10 embeds per message, so send in chunks of 10.
    for i in range(0, len(items), 10):
        chunk = items[i:i + 10]
        embeds = []
        for it in chunk:
            original = it["title"] or "(no title)"
            english = translate_title(original)
            desc = f"¥{it['price']}  •  {it['search_term']}"
            if english and english != original:
                desc += f"\n🇯🇵 {original[:200]}"   # keep the JP title too
            embed = {
                "title": (english or original)[:250],
                "url": it["item_url"],
                "description": desc,
            }
            if it.get("image_url"):
                embed["thumbnail"] = {"url": it["image_url"]}
            embeds.append(embed)
        payload = {"embeds": embeds}
        if i == 0:
            payload["content"] = f"🔔 {len(items)} new item(s)!"
        resp = requests.post(webhook, json=payload)
        if resp.status_code == 429:   # rate limited — wait and retry once
            time.sleep(2)
            requests.post(DISCORD_WEBHOOK, json=payload)
        time.sleep(1)   # stay under Discord's webhook rate limit

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

def parse_flippah(raw_bytes):
    msg = email.message_from_bytes(raw_bytes)
    html, text = "", ""
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/html":
            html = part.get_payload(decode=True).decode("utf-8", errors="replace")
        elif ct == "text/plain":
            text = part.get_payload(decode=True).decode("utf-8", errors="replace")

    item_ids = []
    for m in re.findall(r'open-ebay/(\d+)/', html):
        if m not in item_ids:
            item_ids.append(m)
    images = re.findall(r'(https://i\.ebayimg\.com/[^"\']+)', html)

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    start = 0
    for i, l in enumerate(lines):
        if l.startswith("Note:") and "Clicking" in l:
            start = i + 1
            break
    blocks, buf = [], []
    for l in lines[start:]:
        buf.append(l)
        if l.startswith("Your Search Term:"):
            blocks.append(buf); buf = []

    items = []
    for idx, block in enumerate(blocks):
        price = None
        for l in block:
            pm = re.search(r'\$(\d+(?:\.\d{2})?)', l)
            if pm:
                price = float(pm.group(1)); break
        term = ""
        for l in block:
            if l.startswith("Your Search Term:"):
                term = l.split(":", 1)[1].strip(); break
        item_id = item_ids[idx] if idx < len(item_ids) else None
        items.append({
            "title": block[0],
            "price": price,
            "search_term": term,
            "item_url": f"https://www.ebay.com/itm/{item_id}" if item_id else "",
            "image_url": images[idx] if idx < len(images) else None,
        })
    return items

async def scrape_ebay():
    new_items = []
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("Gmail creds missing, skipping Flippah")
        return new_items
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        imap.select("INBOX")
        typ, data = imap.search(None, 'X-GM-RAW', '"from:flippah.net newer_than:1d"')
        ids = data[0].split()
        print(f"Flippah emails found: {len(ids)}")
        for eid in ids:
            typ, msg_data = imap.fetch(eid, "(RFC822)")
            raw = msg_data[0][1]
            for item in parse_flippah(raw):
                if item["item_url"]:
                    new_items.extend(dedupe_and_store([item], item["search_term"]))
        imap.logout()
    except Exception as e:
        print(f"  Flippah/Gmail error: {e}")
    print(f"  -> {len(new_items)} new eBay items")
    return new_items

async def main():
    print(f"Starting monitor at {datetime.now()}")
    mercari_new = []
    ebay_new = []
    
    # Mercari
    for term in SEARCH_TERMS:
        items = await scrape_term(term)
        mercari_new.extend(dedupe_and_store(items, term))
    
    # eBay
    ebay_items = await scrape_ebay()
    ebay_new.extend(dedupe_and_store(ebay_items, "eBay"))
    
    # Discord alerts to separate channels
    if mercari_new:
        send_discord_alert(mercari_new, webhook=DISCORD_WEBHOOK)
    if ebay_new:
        send_discord_alert(ebay_new, webhook=DISCORD_WEBHOOK_EBAY)
    
    print(f"Done. {len(mercari_new)} Mercari, {len(ebay_new)} eBay new items.")

if __name__ == "__main__":
    asyncio.run(main())
