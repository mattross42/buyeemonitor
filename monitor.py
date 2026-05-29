import requests
import os
from datetime import datetime
from supabase import create_client, Client

# Get secrets from GitHub
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

# Your search terms - EDIT THESE
SEARCH_TERMS = [
    "disney pin",
    "ディズニーピン"
]

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def run_apify_scraper(search_term: str):
    """Call Apify actor to scrape Buyee"""
    print(f"Scraping Buyee for: {search_term}")
    
    response = requests.post(
        f"https://api.apify.com/v2/acts/123webdata~buyee-scraper/runs",
        json={
            "searchQuery": search_term,
            "maxResults": 50
        },
        headers={"Authorization": f"Bearer {APIFY_TOKEN}"}
    )
    
    if response.status_code != 201:
        print(f"Error: {response.text}")
        return []
    
    run_id = response.json()['data']['id']
    print(f"Actor run started: {run_id}")
    
    # Wait for completion
    import time
    while True:
        status_response = requests.get(
            f"https://api.apify.com/v2/acts/123webdata~buyee-scraper/runs/{run_id}",
            headers={"Authorization": f"Bearer {APIFY_TOKEN}"}
        )
        status = status_response.json()['data']['status']
        
        if status in ['SUCCEEDED', 'FAILED']:
            break
        time.sleep(5)
    
    # Get results
    results_response = requests.get(
        f"https://api.apify.com/v2/acts/123webdata~buyee-scraper/runs/{run_id}/dataset/items",
        headers={"Authorization": f"Bearer {APIFY_TOKEN}"}
    )
    
    return results_response.json() if results_response.status_code == 200 else []

def dedupe_and_store(items, search_term):
    """Store items in database and find new ones"""
    new_items = []
    
    for item in items:
        try:
            item_data = {
                "id": item.get('id', item.get('url', '')),
                "item_url": item.get('url'),
                "title": item.get('title'),
                "price": float(str(item.get('price', '0')).replace('¥', '').replace(',', '') or '0'),
                "seller": item.get('seller', 'Unknown'),
                "condition": item.get('condition', 'N/A'),
                "search_term": search_term,
                "image_url": item.get('image'),
                "last_seen": datetime.now().isoformat()
            }
            
            # Check if item already exists
            existing = supabase.table('buyee_items').select('*').eq(
                'item_url', item_data['item_url']
            ).execute()
            
            if existing.data:
                # Item seen before - update last_seen
                supabase.table('buyee_items').update({
                    "last_seen": datetime.now().isoformat(),
                    "is_new": False
                }).eq('item_url', item_data['item_url']).execute()
            else:
                # New item!
                supabase.table('buyee_items').insert(item_data).execute()
                new_items.append(item_data)
                print(f"✨ NEW: {item_data['title']} - ¥{item_data['price']:,.0f}")
        
        except Exception as e:
            print(f"Error processing item: {e}")
            continue
    
    return new_items

def send_discord_alert(items):
    """Send new items to Discord"""
    if not items or not DISCORD_WEBHOOK:
        return
    
    message = f"🔔 Found {len(items)} new item(s)!\n\n"
    for item in items[:5]:  # Show first 5
        message += f"**{item['title']}**\n"
        message += f"¥{item['price']:,.0f} | {item['search_term']}\n"
        message += f"{item['item_url']}\n\n"
    
    payload = {"content": message}
    requests.post(DISCORD_WEBHOOK, json=payload)

def main():
    print(f"Starting Buyee monitor at {datetime.now()}")
    all_new_items = []
    
    for term in SEARCH_TERMS:
        try:
            items = run_apify_scraper(term)
            new_items = dedupe_and_store(items, term)
            all_new_items.extend(new_items)
        except Exception as e:
            print(f"Error scraping {term}: {e}")
    
    if all_new_items:
        send_discord_alert(all_new_items)
        print(f"Found {len(all_new_items)} new items")
    
    print("Monitor complete")

if __name__ == "__main__":
    main()
