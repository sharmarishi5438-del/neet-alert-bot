import json
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
IS_MANUAL_RUN = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

URLS_TO_CHECK = [
    "https://amruhp.ac.in/home-general-notifications/",
    "https://amruhp.ac.in/counselling-notifications/",
]

KEYWORDS = [
    "neet ug", "neet-ug", "mbbs/bds", "mbbs / bds", "mbbs", "bds",
    "state counselling", "state counseling", "centralized counselling",
    "centralised counselling", "counselling notification", "counselling schedule",
    "choice filling", "seat allotment", "merit list",
]

STATE_FILE = "seen_notices.json"


def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_links):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_links), f, indent=2)
    with open("last_checked.txt", "w", encoding="utf-8") as f:
        f.write(f"Last checked: {datetime.now(timezone.utc).isoformat()}\n")


def is_relevant(title):
    t = title.lower()
    return any(kw in t for kw in KEYWORDS)


def fetch_notices(url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; NoticeWatcher/1.0)"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    notices = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if text and (href.lower().endswith(".pdf") or "wp-content/uploads" in href):
            notices.append((text, href))
    return notices


def send_telegram_message(text):
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False}
    r = requests.post(api_url, data=payload, timeout=20)
    if r.status_code != 200:
        print(f"Failed to send Telegram message: {r.status_code} {r.text}")


def main():
    seen = load_seen()
    new_seen = set(seen)
    found_new = []
    first_run = len(seen) == 0

    for url in URLS_TO_CHECK:
        try:
            notices = fetch_notices(url)
        except Exception as e:
            print(f"Could not fetch {url}: {e}")
            continue
        for title, link in notices:
            if link in seen:
                continue
            new_seen.add(link)
            if is_relevant(title) and not first_run:
                found_new.append((title, link))

    for title, link in found_new:
        send_telegram_message(f"🔔 New AMRU HP Notification!\n\n{title}\n\n{link}")

    if IS_MANUAL_RUN:
        send_telegram_message(
            f"✅ Bot is connected and working.\nTracking {len(new_seen)} notices so far.\n"
            f"You'll get a message here the moment a new NEET UG counselling notice appears."
        )

    save_seen(new_seen)


if __name__ == "__main__":
    main()
