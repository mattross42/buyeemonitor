import os
import asyncio
from datetime import datetime
import requests
from supabase import create_client, Client
from mercapi import Mercapi
from mercapi.requests import SearchRequestData

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

# Plain keywords now (NOT URLs). Japanese or English both work. Edit freely.
SEARCH_TERMS = [
    "ディズニー ピン",
    "ピクサー ピン",
    "スター・ウォーズ ピン",
    "ステッチ ピン",
    "disney pin",
]

LIMIT_PER_SEARCH = 20  # newest N per search; it's free now, so tune as you like

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
m = Mercapi()

async def scrape_term(term):
    print(f"Searching: {term}")
    try:
        results = await m.search(
            term,
            sort_by=SearchRequestData.SortBy.SORT_CREATED_TIME,
            sort_order=SearchRequestData.SortOrder.ORDER_DESC,
            status=[SearchRequestData.Status.STATUS_ON_SALE],
        )
    except Exception as e:
        print(f"  search error: {e}")
        return []
    items = results.items[:LIMIT_PER_SEARCH]
    print(f"  -> {len(items)} items (of {results.meta.num_found} total)")
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

def send_discord_alert(items):
    if not items or not DISCORD_WEBHOOK:
        return
    msg = f"🔔 {len(items)} new item(s)!\n\n"
    for it in items[:5]:
        msg += f"**{it['title']}**\n¥{it['price']} | {it['search_term']}\n{it['item_url']}\n\n"
    requests.post(DISCORD_WEBHOOK, json={"content": msg})

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
