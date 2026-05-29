import requests
import os
import json
from datetime import datetime
from supabase import create_client, Client

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

# IMPORTANT: these must be Buyee URLs (search/category pages), NOT plain keywords.
SEARCH_URLS = [
    "https://buyee.jp/mercari/search?keyword=ディズニー&category_id=975&status=on_sale&order-sort=desc-created_time",
    "https://buyee.jp/mercari/search?keyword=disney&category_id=975&status=on_sale&order-sort=desc-created_time",
    "https://buyee.jp/mercari/search?keyword=ピクサー&category_id=975&order-sort=desc-created_time&currencyCode=USD&status=on_sale",
    "https://buyee.jp/mercari/search?keyword=スター%20ウォーズ&category_id=975&order-sort=desc-created_time&currencyCode=USD&status=on_sale",
    "https://buyee.jp/mercari/search?keyword=ステッチ&category_id=975&order-sort=desc-created_time&currencyCode=USD&status=on_sale",
    "https://buyee.jp/item/search/category/44364/?translationType=",
    "https://buyee.jp/item/crosssearch?conversionType=top_page_search&query=disney+pin",
    "https://buyee.jp/item/crosssearch?query=ディズニーピン",
    
]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def run_apify_scraper(search_url):
    print(f"Scraping: {search_url}")
    resp = requests.post(
        "https://api.apify.com/v2/acts/123webdata~buyee-scraper/run-sync-get-dataset-items",
        params={"token": APIFY_TOKEN},
        json={
            "categoryUrls": [search_url],
            "maxResultsPerScrape": 30,
            "usePagination": False
        },
        timeout=300
    )
    if resp.status_code not in (200, 201):
        print(f"  Error {resp.status_code}: {resp.text[:300]}")
        return []
    items = resp.json()
    print(f"  -> got {len(items)} items")
    if items:
        print("RAW FIRST ITEM:", json.dumps(items[0], ensure_ascii=False)[:1200])
    return items

def dedupe_and_store(items, search_url):
    new_items = []
    for item in items:
        try:
            url = item.get('url') or item.get('productUrl') or item.get('link')
            item_data = {
                "id": str(item.get('id') or url or ''),
                "item_url": url,
                "title": item.get('title') or item.get('name'),
                "price": item.get('price'),
                "seller": item.get('seller') or 'Unknown',
                "condition": item.get('condition') or 'N/A',
                "search_term": search_url,
                "image_url": item.get('image') or item.get('imageUrl'),
                "last_seen": datetime.now().isoformat()
            }
            if not item_data["item_url"]:
                continue
            existing = supabase.table('buyee_items').select('item_url').eq(
                'item_url', item_data['item_url']
            ).execute()
            if existing.data:
                supabase.table('buyee_items').update({
                    "last_seen": datetime.now().isoformat(),
                    "is_new": False
                }).eq('item_url', item_data['item_url']).execute()
            else:
                supabase.table('buyee_items').insert(item_data).execute()
                new_items.append(item_data)
                print(f"  NEW: {item_data['title']}")
        except Exception as e:
            print(f"  Error processing item: {e}")
            continue
    return new_items

def send_discord_alert(items):
    if not items or not DISCORD_WEBHOOK:
        return
    message = f"🔔 Found {len(items)} new item(s)!\n\n"
    for item in items[:5]:
        message += f"**{item['title']}**\n{item['price']}\n{item['item_url']}\n\n"
    requests.post(DISCORD_WEBHOOK, json={"content": message})

def main():
    print(f"Starting Buyee monitor at {datetime.now()}")
    all_new_items = []
    for url in SEARCH_URLS:
        try:
            items = run_apify_scraper(url)
            all_new_items.extend(dedupe_and_store(items, url))
        except Exception as e:
            print(f"Error scraping {url}: {e}")
    if all_new_items:
        send_discord_alert(all_new_items)
        print(f"Found {len(all_new_items)} new items")
    print("Monitor complete")

if __name__ == "__main__":
    main()
