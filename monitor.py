import os
import time
import asyncio
from datetime import datetime
import requests
from deep_translator import GoogleTranslator
from supabase import create_client, Client
from mercapi import Mercapi
from mercapi.requests import SearchRequestData

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

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
        
def send_discord_alert(items):
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
        resp = requests.post(DISCORD_WEBHOOK, json=payload)
        if resp.status_code == 429:   # rate limited — wait and retry once
            time.sleep(2)
            requests.post(DISCORD_WEBHOOK, json=payload)
        time.sleep(1)   # stay under Discord's webhook rate limit

async def main():
    print(f"Starting Mercari monitor at {datetime.now()}")
    all_new = []
    for term in SEARCH_TERMS:
        items = await scrape_term(term)
        all_new.extend(dedupe_and_store(items, term))
    if all_new:
        send_discord_alert(all_new)
    print(f"Done. {len(all_new)} new items.")

if __name__ == "__main__":
    asyncio.run(main())
