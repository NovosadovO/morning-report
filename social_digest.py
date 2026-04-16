#!/usr/bin/env python3
"""
Social Digest — надсилає дайджест новин в Telegram двічі на день.
Джерела: RSS блоги/новини Avalanche, Ethereum, AAVE, CoinDesk,
         CoinTelegraph, UEFA CL, Netflix, CoinMarketCap, PharaohExchange
"""

import os
import json
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error
import html
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]
SEEN_FILE      = "/tmp/social_digest_seen.json"

# ─── FEEDS ────────────────────────────────────────────────────────────────────
FEEDS = [
    {"name": "🔺 Avalanche",            "url": "https://medium.com/feed/avalanche-hub"},
    {"name": "💎 Ethereum Foundation",  "url": "https://blog.ethereum.org/feed.xml"},
    {"name": "🏦 AAVE",                "url": "https://medium.com/feed/aave"},
    {"name": "📰 CoinDesk",            "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "📰 CoinTelegraph",        "url": "https://cointelegraph.com/rss"},
    {"name": "📊 CoinMarketCap",        "url": "https://news.google.com/rss/search?q=CoinMarketCap&hl=en&gl=US&ceid=US:en"},
    {"name": "⚽ UEFA Champions League","url": "https://news.google.com/rss/search?q=UEFA+Champions+League&hl=en&gl=US&ceid=US:en"},
    {"name": "🎬 Netflix",              "url": "https://news.google.com/rss/search?q=Netflix&hl=en&gl=US&ceid=US:en"},
    {"name": "🏺 PharaohExchange",      "url": "https://news.google.com/rss/search?q=PharaohExchange&hl=en&gl=US&ceid=US:en"},
]

# Скільки годин назад рахувати "новим"
HOURS_BACK = 7  # покриває проміжок між двома запусками (12:00 і 19:00)

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Telegram обмеження 4096 символів
    text = text[:4090]
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }).encode()
    req = urllib.request.Request(url, data=payload,
          headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def fetch_rss(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; digest-bot/1.0)"
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"RSS fetch error {url}: {e}")
        return None


def parse_rss(content: str) -> list[dict]:
    """Parse RSS/Atom feed, return list of {title, link, published}"""
    items = []
    try:
        root = ET.fromstring(content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        # Atom feed
        if root.tag in ("{http://www.w3.org/2005/Atom}feed", "feed"):
            for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
                title = entry.findtext("{http://www.w3.org/2005/Atom}title", "")
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                link = link_el.get("href", "") if link_el is not None else ""
                pub = entry.findtext("{http://www.w3.org/2005/Atom}updated") or \
                      entry.findtext("{http://www.w3.org/2005/Atom}published") or ""
                items.append({"title": html.unescape(title.strip()), "link": link.strip(), "published": pub})
        else:
            # RSS 2.0
            for item in root.iter("item"):
                title = item.findtext("title", "")
                link  = item.findtext("link", "") or item.findtext("guid", "")
                pub   = item.findtext("pubDate", "") or item.findtext("dc:date", "")
                items.append({"title": html.unescape(title.strip()), "link": link.strip(), "published": pub})
    except Exception as e:
        print(f"RSS parse error: {e}")
    return items


def parse_date(s: str) -> datetime | None:
    """Try to parse various date formats"""
    if not s:
        return None
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_seen() -> set:
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-1000:], f)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=HOURS_BACK)
    seen = load_seen()
    new_seen = set()

    print(f"=== Social Digest at {now.isoformat()} ===")
    print(f"Cutoff: {cutoff.isoformat()}")

    sections = []

    for feed in FEEDS:
        name = feed["name"]
        url  = feed["url"]
        print(f"Fetching {name}...")

        content = fetch_rss(url)
        if not content:
            continue

        items = parse_rss(content)
        new_items = []

        for item in items[:20]:  # перші 20
            link  = item["link"]
            title = item["title"]
            pub   = item["published"]

            if not title or not link:
                continue

            # Дедуплікація
            uid = link or title
            if uid in seen:
                continue

            # Фільтр по часу
            dt = parse_date(pub)
            if dt and dt < cutoff:
                continue

            new_seen.add(uid)
            new_items.append(f'• <a href="{link}">{esc(title[:80])}</a>')

        if new_items:
            sections.append(f"<b>{name}</b>\n" + "\n".join(new_items[:5]))
            print(f"  {len(new_items)} new items")
        else:
            print(f"  Nothing new")

    # Update seen
    save_seen(seen | new_seen)

    if not sections:
        print("No new content, skipping Telegram message")
        return

    hour_local = (now.hour + 2) % 24  # UTC+2 Košice
    period = "ранковий 🌅" if hour_local < 15 else "вечірній 🌆"

    header = f"📱 <b>Дайджест новин — {period}</b>\n{now.strftime('%d.%m.%Y')}\n\n"
    body = "\n\n".join(sections)
    full_msg = header + body

    # Розбиваємо якщо більше 4000 символів
    if len(full_msg) <= 4000:
        send_telegram(full_msg)
    else:
        # Надсилаємо по частинах
        send_telegram(header + sections[0])
        chunk = ""
        for s in sections[1:]:
            if len(chunk) + len(s) + 2 > 3800:
                send_telegram(chunk)
                chunk = s
            else:
                chunk += "\n\n" + s if chunk else s
        if chunk:
            send_telegram(chunk)

    print(f"=== Done — {len(sections)} sources with new content ===")


if __name__ == "__main__":
    main()
