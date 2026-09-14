import json
import os
import re
import smtplib
from email.mime.text import MIMEText
from urllib.parse import urljoin

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
        # NARROWED (per request): only her B.Sc Nursing track — result,
        # counselling, and merit lists (provisional + final). No NEET UG/
        # MBBS/BDS/MDS/Allied Health, no M.Sc Nursing, no Post Basic B.Sc
        # Nursing (a different program from a different candidate pool),
        # and no routine semester/supplementary exam notices for already-
        # enrolled students.
        "label": "🩺 AMRU HP — B.Sc Nursing",
        # FIXED: this page loads its notice list via JS/AJAX, so a plain
        # requests+BeautifulSoup fetch sees an almost-empty page — that's why
        # nothing was ever detected. The real notice links (results, answer
        # keys, merit lists, seat allocation, all as plain <a href> tags) live
        # directly on the homepage itself.
        "urls": [
            "https://amruhp.ac.in/",
        ],
        "pattern": "pdf_or_wp_uploads",
        # A title only needs to mention her course somewhere — this alone
        # covers results, answer keys, counselling schedules, choice filling,
        # seat allocation and merit lists, since AMRU always names the course
        # in the title regardless of notice type.
        "keywords": ["bsc nursing", "b.sc.(n)"],
        # "Post Basic B.Sc. Nursing" is a separate program for already-
        # practicing nurses — strip it out before matching so a title that's
        # ONLY about Post Basic (or M.Sc + Post Basic) doesn't false-match on
        # the "bsc nursing" substring buried inside it. A title that mentions
        # her plain "B.Sc. Nursing" *in addition to* Post Basic still matches
        # normally, since the plain mention survives the strip.
        "strip_before_match": ["post basic b.sc. nursing", "post basic bsc(n)"],
        # Routine academic notices for already-enrolled students, not
        # entrance/counselling related — filtered out even if they mention
        # her course by name.
        "exclude_keywords": ["semester", "supplementary", "retotal", "reappear", "date sheet", "annual"],
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


def normalize(text):
    # FIXED: AMRU writes the same course name several different ways in
    # their own notices — "B.Sc. Nursing", "B. Sc. Nursing", "BSc Nursing" —
    # and the old exact-substring keyword check silently missed anything
    # that didn't match the punctuation/spacing of the keyword list exactly.
    # Stripping all periods/whitespace before comparing makes matching
    # immune to that formatting inconsistency.
    text = text.lower()
    return re.sub(r"[.\s]+", "", text)


def is_relevant(title, source):
    keywords = source.get("keywords")
    if keywords is None:
        return True
    t = normalize(title)
    matchable = t
    for phrase in source.get("strip_before_match", []):
        matchable = matchable.replace(normalize(phrase), "")
    if not any(normalize(kw) in matchable for kw in keywords):
        return False
    exclude = source.get("exclude_keywords") or []
    if any(normalize(kw) in t for kw in exclude):
        return False
    return True


def fetch_notices(url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; NoticeWatcher/1.0)"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if not text:
            continue
        # FIXED: resolve relative hrefs (e.g. "/wp-content/...") against the
        # page URL so links sent to Telegram/email are always clickable.
        href = urljoin(url, a["href"])
        results.append((text, href))
    return results


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
                if is_relevant(title, source) and not first_run:
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
