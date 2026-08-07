import json
import os
import smtplib
from email.mime.text import MIMEText
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
EMAIL_ADDRESS = os.environ["EMAIL_ADDRESS"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]
IS_MANUAL_RUN = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

SOURCES = [
    {
        "label": "🩺 AMRU HP (NEET UG + BSc Nursing)",
        "urls": [
            "https://amruhp.ac.in/home-general-notifications/",
            "https://amruhp.ac.in/counselling-notifications/",
        ],
        "pattern": "pdf_or_wp_uploads",
        "keywords": [
            "neet ug", "neet-ug", "mbbs/bds", "mbbs / bds", "mbbs", "bds",
            "state counselling", "state counseling", "centralized counselling",
            "centralised counselling", "counselling notification", "counselling schedule",
            "choice filling", "seat allotment", "merit list",
            "bsc nursing", "b.sc. nursing", "b.sc nursing", "bsc. nursing",
        ],
    },
    {
        "label": "📘 JEE Main",
        "urls": ["https://jeemain.nta.nic.in/public-notices/"],
        "pattern": "pdf_only",
        "keywords": None,
    },
    {
        "label": "🎓 JoSAA (Counselling)",
        "urls": ["https://josaa.nic.in/news-event/"],
        "pattern": "josaa_document",
        "keywords": None,
    },
    {
        "label": "📝 CBSE (Board Exam)",
        "urls": ["https://www.cbse.gov.in/cbsenew/examination_Circular.html"],
        "pattern": "cbse_documents",
        "keywords": None,
    },
]

STATE_FILE = "seen_notices.json"
CHECKED_FILE = "last_checked.txt"
HEARTBEAT_FILE = "last_heartbeat.txt"
HEARTBEAT_INTERVAL = timedelta(hours=1)


def ensure_file_exists(path, default_content):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(default_content)


def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_links):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_links), f, indent=2)
    with open(CHECKED_FILE, "w", encoding="utf-8") as f:
        f.write(f"Last checked: {datetime.now(timezone.utc).isoformat()}\n")


def link_matches_pattern(href, pattern):
    h = href.lower()
    if pattern == "pdf_or_wp_uploads":
        return h.endswith(".pdf") or "wp-content/uploads" in h
    if pattern == "pdf_only":
        return h.endswith(".pdf")
    if pattern == "cbse_documents":
        return "cbse.gov.in/cbsenew/documents/" in h
    if pattern == "josaa_document":
        return "/document/" in h
    return False


def is_relevant(title, keywords):
    if keywords is None:
        return True
    t = title.lower()
    return any(kw in t for kw in keywords)


def fetch_notices(url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; NoticeWatcher/1.0)"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    return [(a.get_text(strip=True), a["href"]) for a in soup.find_all("a", href=True) if a.get_text(strip=True)]


def send_telegram_message(text):
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False}
    r = requests.post(api_url, data=payload, timeout=20)
    if r.status_code != 200:
        print(f"Failed to send Telegram message: {r.status_code} {r.text}")


def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = EMAIL_ADDRESS
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")


def should_send_heartbeat():
    with open(HEARTBEAT_FILE, "r", encoding="utf-8") as f:
        last_str = f.read().strip()
    try:
        last_time = datetime.fromisoformat(last_str)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last_time >= HEARTBEAT_INTERVAL


def mark_heartbeat_sent():
    with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat())


def main():
    ensure_file_exists(STATE_FILE, "[]")
    ensure_file_exists(CHECKED_FILE, "Last checked: never\n")
    ensure_file_exists(HEARTBEAT_FILE, "1970-01-01T00:00:00+00:00")

    seen = load_seen()
    new_seen = set(seen)
    found_new = []
    source_counts = {}
    first_run = len(seen) == 0

    for source in SOURCES:
        count_for_source = 0
        for url in source["urls"]:
            try:
                notices = fetch_notices(url)
            except Exception as e:
                print(f"Could not fetch {url}: {e}")
                continue
            for title, href in notices:
                if not link_matches_pattern(href, source["pattern"]):
                    continue
                count_for_source += 1
                if href in seen:
                    continue
                new_seen.add(href)
                if is_relevant(title, source["keywords"]) and not first_run:
                    found_new.append((source["label"], title, href))
        source_counts[source["label"]] = count_for_source

    for label, title, link in found_new:
        send_telegram_message(f"🔔 {label}\n\n{title}\n\n{link}")
        send_email(f"🔔 New notice — {label}", f"{title}\n\n{link}")

    breakdown_lines = "\n".join(f"  • {label}: {count} tracked" for label, count in source_counts.items())

    if IS_MANUAL_RUN:
        msg = f"✅ Bot is connected and working.\n\n{breakdown_lines}\n\nYou'll get a message the moment something new shows up."
        send_telegram_message(msg)
        send_email("✅ Bot connected — test run", msg)
    elif should_send_heartbeat():
        now_str = datetime.now(timezone.utc).strftime("%d %b, %I:%M %p UTC")
        send_telegram_message(f"🟢 Still running fine — {now_str}\n\n{breakdown_lines}")

        urls_lines = "\n".join(f"{s['label']}:\n  " + "\n  ".join(s["urls"]) for s in SOURCES)
        email_body = (
            f"Status report — {now_str}\n\nTracked notices by source:\n{breakdown_lines}\n\n"
            f"New notices found this run: {len(found_new)}\n\nPages being watched:\n{urls_lines}\n\n"
            f"Checked every ~15 minutes automatically. No action needed."
        )
        send_email("📊 NEET/JEE/CBSE Bot — Hourly Status", email_body)
        mark_heartbeat_sent()

    save_seen(new_seen)


if __name__ == "__main__":
    main()
