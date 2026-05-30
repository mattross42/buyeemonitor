import os
import time
import asyncio
from datetime import datetime
import requests
import feedparser
import xml.etree.ElementTree as ET
from deep_translator import GoogleTranslator
from supabase import create_client, Client
from mercapi import Mercapi
from mercapi.requests import SearchRequestData

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
DISCORD_WEBHOOK_EBAY = os.getenv("DISCORD_WEBHOOK_EBAY")

# Plain keywords now (NOT URLs). Japanese or English both work. Edit freely.
PIN_CATEGORY = 975  # キャラクターグッズ → ピンズ・ピンバッジ・缶バッジ; applied to every search

# Plain keywords. Each is auto-filtered to the pin/badge category above.
# Cut any lines you don't collect — fewer terms = faster, lighter runs.
SEARCH_TERMS = [
    # --- Disney umbrella ---
    "ディズニー", "ディズニーランド", "ディズニーシー", "ディズニーリゾート", "TDR", "ディズニーストア",
    # --- Mickey & friends ---
    "ミッキー", "ミニー", "ドナルド", "デイジー", "グーフィー", "プルート", "チップとデール", "チップ&デール",
    # --- Winnie the Pooh ---
    "くまのプーさん", "プーさん", "ピグレット", "ティガー", "イーヨー",
    # --- Princesses & their films ---
    "白雪姫", "シンデレラ", "オーロラ姫", "眠れる森の美女", "アリエル", "リトルマーメイド",
    "ベル", "美女と野獣", "ジャスミン", "アラジン", "ラプンツェル", "塔の上のラプンツェル",
    "モアナ", "ティアナ", "ムーラン", "メリダ",
    # --- Frozen ---
    "アナと雪の女王", "エルサ", "オラフ",
    # --- Classics & other films ---
    "ふしぎの国のアリス", "アリス", "チェシャ猫", "ピーターパン", "ティンカーベル",
    "ダンボ", "バンビ", "ピノキオ", "ライオンキング", "シンバ", "ヘラクレス", "ノートルダムの鐘",
    # --- Villains ---
    "マレフィセント", "アースラ", "クルエラ",
    # --- Nightmare Before Christmas ---
    "ナイトメアビフォアクリスマス", "ナイトメア", "ジャックスケリントン", "ゼロ",
    # --- Stitch / Lilo & Stitch ---
    "スティッチ", "リロ&スティッチ", "エンジェル", "スクランプ",
    # --- Pixar ---
    "ピクサー", "トイストーリー", "ウッディ", "バズライトイヤー",
    "リトルグリーンメン", "エイリアン", "モンスターズインク",
    "ファインディングニモ", "ニモ", "ドリー", "カーズ", "マックィーン",
    "レミーのおいしいレストラン", "ウォーリー", "カールじいさんの空飛ぶ家",
    "インサイドヘッド",
    "私ときどきレッサーパンダ", "ベイマックス", "ズートピア",
    # --- Star Wars (both spellings) ---
    "スターウォーズ", "スター・ウォーズ", "STAR WARS", "ダースベイダー", "ヨーダ",
    "グローグー", "ベビーヨーダ", "マンダロリアン", "R2-D2", "C-3PO", "BB-8",
    "ストームトルーパー", "チューバッカ", "ボバフェット", "レイ", "カイロレン",
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
            item_id = getattr(it, "id_", None) or getattr(it, "id", None)
            if not item_id:
                continue
            buyee_url = f"https://buyee.jp/mercari/item/{item_id}"
            thumbs = getattr(it, "thumbnails", None) or []
            item_data = {
                "id": str(item_id),
                "item_url": buyee_url,
                "title": getattr(it, "name", None),
                "price": getattr(it, "price", None),
                "seller": "Mercari",
                "condition": "N/A",
                "search_term": term,
                "image_url": thumbs[0] if thumbs else None,
                "last_seen": datetime.now().isoformat(),
            }
            existing = supabase.table("buyee_items").select("item_url").eq(
                "item_url", buyee_url
            ).execute()
            if existing.data:
                supabase.table("buyee_items").update({
                    "last_seen": datetime.now().isoformat(),
                    "is_new": False,
                }).eq("item_url", buyee_url).execute()
            else:
                supabase.table("buyee_items").insert(item_data).execute()
                new_items.append(item_data)
                print(f"  NEW: {item_data['title']} - ¥{item_data['price']}")
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

async def scrape_ebay():
    new_items = []
    for keyword, min_price in EBAY_SEARCH_TERMS:
        print(f"Searching eBay: {keyword}" + (f" (min ${min_price})" if min_price else ""))
        try:
            url = f"https://www.ebay.com/rss/search/listings?_nkw={keyword}&_sop=12&_ipg=100"
            if min_price:
                url += f"&_udlo={min_price}"
            feed = feedparser.parse(url)
            print(f"  -> {len(feed.entries)} items")
            
            for entry in feed.entries[:50]:
                title = entry.get("title", "(no title)")
                ebay_url = entry.get("link", "")
                price_str = entry.get("summary", "")
                image_url = None
                
                # Extract price and image from summary HTML
                import re
                price_match = re.search(r'\$(\d+(?:\.\d{2})?)', price_str)
                price = float(price_match.group(1)) if price_match else None
                img_match = re.search(r'<img[^>]*src=["\']([^"\']+)["\']', price_str)
                if img_match:
                    image_url = img_match.group(1)
                
                item_data = {
                    "item_url": ebay_url,
                    "title": title,
                    "price": price,
                    "search_term": keyword,
                    "image_url": image_url,
                }
                if await dedup_and_store(item_data, keyword):
                    new_items.append(item_data)
        except Exception as e:
            print(f"  eBay error: {e}")
        
        await asyncio.sleep(0.5)
    
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
