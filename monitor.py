#!/usr/bin/env python3
"""
Monitor — надсилає один зведений звіт кожні 3 години.
"""

import os
import re
import json
import base64
import imaplib
import email
import email.header
import urllib.request
import urllib.error
import urllib.parse
import time
from datetime import datetime, timezone, timedelta
import storage

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT   = os.environ["TELEGRAM_CHAT_ID"]
GMAIL_USER      = "novosadovoleg@gmail.com"
GMAIL_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")
_DATA_DIR       = os.path.dirname(os.path.abspath(__file__))
SEEN_EMAIL_FILE = os.path.join(_DATA_DIR, "monitor_seen_emails.json")
# Ціновий кеш в /tmp — зберігається між циклами але скидається при деплої
PRICE_CACHE     = "/tmp/monitor_prices_3h.json"
# PRICE_HISTORY moved to GitHub storage via storage.load_price_history() / save_price_history()

COINS = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "BNB":  "binancecoin",
    "XRP":  "ripple",
    "SOL":  "solana",
    "DOGE": "dogecoin",
    "ADA":  "cardano",
    "TRX":  "tron",
    "LINK": "chainlink",
    "AVAX": "avalanche-2",
    "TON":  "the-open-network",
    "XLM":  "stellar",
    "HBAR": "hedera-hashgraph",
    "SUI":  "sui",
    "BCH":  "bitcoin-cash",
    "LTC":  "litecoin",
    "DOT":  "polkadot",
    "HYPE": "hyperliquid",
    "XMR":  "monero",
    "ONDO": "ondo-finance",
}

# Алерти >5% ТІЛЬКИ для монет Олега
ALERT_COINS = {"BTC", "ETH", "AVAX", "ONDO"}

# ─── EMAIL CLASSIFICATION ─────────────────────────────────────────────────────
# Рівні: SPAM (викинути) → PROMO (показати в "Інші") → REAL (основні)

# Домени/ключові слова відправника — одразу в смітник
_SPAM_SENDERS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "newsletter", "notifications", "mailer", "marketing", "unsubscribe",
    "digest", "updates@", "news@", "alert@binance", "alert@coinbase",
    "notify.railway", "temu", "footshop", "temuemail",
    "unstoppabledomains", "startengine",
    "jobvite", "greenhouse", "workday", "lever.co",
    "okx", "roundup", "dlnews", "coindesk", "cointelegraph",
    "decrypt.co",
    "tripadvis", "booking.com", "sg.booking", "e.tripadvisor", "email.booking",
    "campaign@", "inspiration@", "aboutyou", "hello@news", "deals@", "offers@",
    "uniswap", "investing.com", "coinpoker", "novinky@",
    "sizeer", "pullandbear", "uefa", "store@", "streetguide@",
    "slovnaft", "kaufland", "loyalty", "mp1.", "em.", "info@",
    "finexity", "xtb.com", "zlavomat", "fox.com", "inbox.fox",
    "rondogo", "avax.network", "nft.", "airdrop", "binance.com",
    "support@", "promo@", "hello@", "team@", "hi@",
    "hotels.com", "eg.hotels", "airbnb.com", "expedia",
}

# Суб-домени відправника що означають bulk-mail
_SPAM_SUBDOMAINS = re.compile(
    r'^(news|mail|em|e\d*|m\d*|campaign|email|noreply|no-reply|'
    r'update|notification|send|go|sg|mp\d+|loyalty|kcard|alert|digest|'
    r'bulk|bounce|reply|auto|info|promo|marketing)\.'
)

# Ключові слова в темі листа
_SPAM_SUBJECTS = {
    "newsletter", "digest", "promo", "offer", "sale", "discount",
    "unsubscribe", "your daily", "weekly", "monthly", "referral",
    "new launch", "collecting", "portfolio", "managed by ai",
    "predtým", "teraz", "máš ich",
    "вакансі", "job alert", "new job", "recommended job", "hiring",
    "trading suite", "one step away",
    "national parks", "genius", "watchlist", "satellites",
    "vyberte", "dobierku", "zľava", "výpredaj",
    "% off", "limited time", "exclusive deal", "flash sale",
}

def _classify_email(sender: str, subject: str) -> str:
    """
    Повертає: 'spam' | 'promo' | 'real'
    Логіка базується на EMAIL ДОМЕНІ — не на display name (бо його підробляють).
    """
    s = sender.lower()
    sub = subject.lower()

    # WHITELIST — завжди 'real' незалежно від інших правил
    _WHITELIST_DOMAINS = {
        "theblock.co", "blockworks.co",
        "economist.com",
        "jpmorgan.com", "jpmorganchase.com",
        "linkedin.com",  # LinkedIn newsletters (не job alerts)
    }
    _wl_match = re.search(r'[\w.+%-]+@([\w.-]+\.[a-z]{2,})', s)
    if _wl_match:
        _wl_domain = _wl_match.group(1)
        if _wl_domain in _WHITELIST_DOMAINS:
            # Виключаємо job alerts від LinkedIn
            _job_kw = {"job alert", "new job", "recommended job", "hiring", "вакансі", "jobvite"}
            if not any(kw in sub for kw in _job_kw):
                return "real"

    # Витягуємо email адресу відправника
    email_match = re.search(r'[\w.+%-]+@([\w.-]+\.[a-z]{2,})', s)
    if not email_match:
        return "spam"
    email_addr = email_match.group(0)
    domain = email_match.group(1)  # example.com
    # Верхній рівень домену (TLD): gmail.com → gmail, s-mania.com → s-mania
    domain_parts = domain.split('.')
    root = domain_parts[-2] if len(domain_parts) >= 2 else domain

    # 1. Явний спам по email/домену — drop
    if any(kw in s for kw in _SPAM_SENDERS):
        return "spam"
    if any(kw in sub for kw in _SPAM_SUBJECTS):
        return "spam"

    # 2. Промо піддомен — drop
    if _SPAM_SUBDOMAINS.match(domain):
        return "promo"

    # 3. ОСОБИСТИЙ EMAIL домен → реальна людина
    # gmail, outlook, hotmail, yahoo, ukr.net, icloud, proton, meta.ua тощо
    _PERSONAL_DOMAINS = {
        "gmail", "googlemail",
        "outlook", "hotmail", "live", "msn",
        "yahoo", "ymail",
        "icloud", "me", "mac",
        "ukr", "i", "meta", "ua",
        "proton", "protonmail",
        "tutanota", "tutamail",
        "seznam",
        "azet", "zoznam", "centrum",  # SK домени
        "post", "email",
    }
    if root in _PERSONAL_DOMAINS:
        return "real"

    # 4. Корпоративний домен — перевіряємо чи виглядає як особистий email
    # Ознаки масової розсилки в локальній частині (перед @):
    local = email_addr.split('@')[0]
    _BULK_LOCAL = {
        "noreply", "no-reply", "donotreply", "newsletter", "news",
        "notifications", "notify", "mailer", "marketing", "promo",
        "info", "hello", "hi", "team", "support", "admin", "updates",
        "deals", "offers", "digest", "alert", "alerts", "bulletin",
        "campaign", "email", "mail", "contact", "service", "sales",
        "billing", "reply", "bounce", "postmaster", "welcome",
        "notification", "automated", "system", "bot",
    }
    if any(kw == local or kw in local for kw in _BULK_LOCAL):
        return "spam"

    # 5. Відомі масові сервіси по root домену
    _BULK_ROOTS = {
        "facebook", "instagram", "twitter", "x", "youtube",
        "google", "apple", "amazon", "microsoft",
        "duolingo", "spotify", "netflix", "twitch",
        "substack", "beehiiv", "mailchimp", "sendgrid", "klaviyo",
        "tradingview", "coinmarketcap", "coingecko", "binance",
        "temu", "shopify", "etsy", "ebay",
        "booking", "airbnb", "expedia", "tripadvisor",
        "profesia", "indeed", "glassdoor",
        "s-mania", "smania", "lanet", "railway",
    }
    if root in _BULK_ROOTS:
        return "spam"

    # 6. Корпоративний домен без ознак розсилки → скоріш за все реальна людина
    return "real"

# Зворотна сумісність
IGNORE_SENDERS = list(_SPAM_SENDERS)
IGNORE_SUBJECTS = list(_SPAM_SUBJECTS)

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _html_to_markdown(text: str) -> str:
    """Конвертує HTML теги в MarkdownV2 і екранує спецсимволи."""
    import re as _re

    # Спочатку витягуємо <code>...</code> блоки — їх не чіпаємо всередині
    code_blocks = {}
    def _save_code(m):
        key = f"\x00CODE{len(code_blocks)}\x00"
        inner = m.group(1)
        code_blocks[key] = f"`{inner}`"
        return key
    text = _re.sub(r'<code>(.*?)</code>', _save_code, text, flags=_re.DOTALL)

    # <pre>...</pre>
    def _save_pre(m):
        key = f"\x00PRE{len(code_blocks)}\x00"
        inner = m.group(1)
        code_blocks[key] = f"```\n{inner}\n```"
        return key
    text = _re.sub(r'<pre>(.*?)</pre>', _save_pre, text, flags=_re.DOTALL)

    # <a href="...">text</a>
    text = _re.sub(r'<a\s+href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'[\2](\1)', text, flags=_re.I)

    # <b>/<strong> → *...*
    text = _re.sub(r'<b>(.*?)</b>', r'*\1*', text, flags=_re.I | _re.DOTALL)
    text = _re.sub(r'<strong>(.*?)</strong>', r'*\1*', text, flags=_re.I | _re.DOTALL)

    # <i>/<em> → _..._
    text = _re.sub(r'<i>(.*?)</i>', r'_\1_', text, flags=_re.I | _re.DOTALL)
    text = _re.sub(r'<em>(.*?)</em>', r'_\1_', text, flags=_re.I | _re.DOTALL)

    # <s> → ~...~
    text = _re.sub(r'<s>(.*?)</s>', r'~\1~', text, flags=_re.I | _re.DOTALL)

    # Прибираємо решту тегів
    text = _re.sub(r'<[^>]+>', '', text)

    # Розкодуємо HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')

    # Екрануємо спецсимволи MarkdownV2 (крім того що всередині * _ ` ~)
    # Спецсимволи: _ * [ ] ( ) ~ ` > # + - = | { } . !
    ESCAPE = r'\_[]()~`>#+=|{}.!'
    result = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == '\x00':
            # Знаходимо кінець ключа
            end = text.find('\x00', i + 1)
            if end != -1:
                key = text[i:end+1]
                result.append(code_blocks.get(key, key))
                i = end + 1
                continue
        elif c == '*':
            result.append('*')
        elif c == '_':
            result.append('_')
        elif c == '~':
            result.append('~')
        elif c == '[':
            result.append('[')
        elif c == ']':
            result.append(']')
        elif c == '(':
            result.append('(')
        elif c == ')':
            result.append(')')
        elif c in r'\`>#+=|{}.!':
            result.append('\\' + c)
        elif c == '-':
            result.append('\\-')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def _sanitize_html(text: str) -> str:
    """
    Екранує &, < і > що НЕ є частиною валідних HTML тегів/entities.
    Дозволені теги: <b> <i> <u> <s> <code> <pre> <a href=...> та їх закриваючі.
    """
    import re as _re

    # Крок 1: витягуємо валідні HTML теги і entities — замінюємо на плейсхолдери
    ALLOWED_TAG = r'</?(?:b|i|u|s|code|pre|a(?:\s+href="[^"]*")?)>'
    ENTITY = r'&(?:amp|lt|gt|quot|#\d+|#x[\da-fA-F]+);'

    placeholders = {}
    counter = [0]

    def save(m):
        tag = m.group()
        # Екрануємо & всередині href що ще не є &amp; etc.
        if tag.startswith('<a '):
            tag = _re.sub(r'&(?!amp;|lt;|gt;|quot;|#)', '&amp;', tag)
        key = f'\x00PH{counter[0]}\x00'
        placeholders[key] = tag
        counter[0] += 1
        return key

    # Зберігаємо валідні теги і entities
    protected = _re.sub(f'(?:{ALLOWED_TAG}|{ENTITY})', save, text)

    # Крок 2: екрануємо голі &, <, >
    protected = _re.sub(r'&', '&amp;', protected)
    protected = _re.sub(r'<', '&lt;', protected)
    protected = _re.sub(r'>', '&gt;', protected)

    # Крок 3: відновлюємо плейсхолдери
    for key, val in placeholders.items():
        protected = protected.replace(key, val)

    return protected


def _send_telegram_chunk(text: str) -> bool:
    """Надсилає одне повідомлення з HTML parse_mode."""
    import re as _re
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Автоматично фіксуємо голі & перед відправкою
    text = _sanitize_html(text)
    print(f"[tg_chunk] len={len(text)} preview={repr(text[:120])}", flush=True)

    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text,
        "parse_mode": "HTML"
    }).encode()
    req = urllib.request.Request(url, data=payload,
          headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        print(f"[tg_chunk] HTML error: {e.code} {err_body}", flush=True)
        # Лог перших і останніх 300 символів тексту для діагностики
        print(f"[tg_chunk] TEXT START: {repr(text[:300])}", flush=True)
        print(f"[tg_chunk] TEXT END:   {repr(text[-300:])}", flush=True)
        # Fallback: агресивний whitelist - лишаємо тільки дозволені Telegram теги
        try:
            ALLOWED = {'b', 'i', 'u', 's', 'code', 'pre'}
            # Стрипаємо всі теги крім дозволених
            def _strip_unknown(m):
                tag_inner = m.group(1) or m.group(2)  # "b" / "/b" / "a href=..."
                tag_name = tag_inner.lstrip('/').split()[0].lower()
                if tag_name in ALLOWED:
                    return m.group(0)  # зберігаємо
                return ''  # видаляємо невідомий тег
            clean = _re.sub(r'<(/?\w+[^>]*)>', _strip_unknown, text)
            # Перевіримо баланс - якщо незакриті теги залишились, стрипуємо все
            opens  = _re.findall(r'<(b|i|u|s|code|pre)>', clean)
            closes = _re.findall(r'</(b|i|u|s|code|pre)>', clean)
            if sorted(opens) != sorted(closes):
                print(f"[tg_chunk] unbalanced tags, stripping all HTML", flush=True)
                clean = _re.sub(r'<[^>]+>', '', clean)
            clean = clean.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>')
            payload2 = json.dumps({"chat_id": TELEGRAM_CHAT, "text": clean}).encode()
            req2 = urllib.request.Request(url, data=payload2, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req2, timeout=10) as r2:
                print(f"[tg_chunk] plain fallback OK", flush=True)
                return r2.status == 200
        except Exception as e2:
            print(f"[tg_chunk] plain fallback error: {e2}", flush=True)
        return False
    except Exception as e:
        print(f"[tg_chunk] error: {e}", flush=True)
        return False


def send_telegram(text: str) -> bool:
    """Надсилає текст, автоматично розбиваючи на частини якщо > 4090 символів."""
    MAX = 4090
    if len(text) <= MAX:
        return _send_telegram_chunk(text)

    # Розбиваємо по рядках, не ріжемо слова
    parts = []
    current = ""
    for line in text.split("\n"):
        candidate = current + ("\n" if current else "") + line
        if len(candidate) <= MAX:
            current = candidate
        else:
            if current:
                parts.append(current)
            # Якщо один рядок сам по собі довший MAX — ріжемо
            while len(line) > MAX:
                parts.append(line[:MAX])
                line = line[MAX:]
            current = line
    if current:
        parts.append(current)

    import time as _time
    ok = True
    for i, part in enumerate(parts):
        if i > 0:
            _time.sleep(0.5)
        if not _send_telegram_chunk(part):
            ok = False
    return ok


def _send_telegram_photo(photo_url: str, caption: str) -> bool:
    # Шлемо як анімацію (GIF)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendAnimation"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "animation": "https://storage.googleapis.com/runable-templates/cli-uploads%2F1zsprqn6ymqOFgAJnNEK2HbTycMPBvLc%2F84VzoRtuRjk0i6Ju6EUAd%2Fmail_alert.gif",
        "caption": caption[:1024],
        "parse_mode": "HTML"
    }).encode()
    req = urllib.request.Request(url, data=payload,
          headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"sendAnimation error: {e}")
        return send_telegram(caption)


def _send_photo_bytes(photo_bytes: bytes, caption: str = "") -> bool:
    """Відправляє PNG bytes як фото в Telegram."""
    try:
        import requests as _req_pb
        import io as _io_pb
        r = _req_pb.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": TELEGRAM_CHAT, "caption": caption[:1024], "parse_mode": "HTML"},
            files={"photo": ("chart.png", _io_pb.BytesIO(photo_bytes), "image/png")},
            timeout=25
        )
        ok = r.status_code == 200
        if not ok:
            print(f"_send_photo_bytes error: {r.status_code} {r.text[:200]}")
        return ok
    except Exception as e:
        print(f"_send_photo_bytes exception: {e}")
        return False


def fetch_json(url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            if _HAS_REQUESTS:
                r = _requests.get(url, headers={"User-Agent": "monitor/1.0"}, timeout=20)
                r.raise_for_status()
                return r.json()
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "monitor/1.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.loads(r.read().decode())
        except Exception as e:
            print(f"fetch_json attempt {attempt}/{retries} error [{url[:60]}]: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    return None


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_json_file(path, default=None):
    """Читає JSON. Якщо файл monitor_*.json — спочатку пробує GitHub (persistent)."""
    filename = os.path.basename(path)
    if filename.startswith("monitor_") and filename.endswith(".json"):
        try:
            import storage as _storage
            return _storage.load(filename, default=default if default is not None else {})
        except Exception:
            pass
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json_file(path, data):
    """Зберігає JSON. Якщо файл monitor_*.json — зберігає в GitHub (persistent)."""
    filename = os.path.basename(path)
    if filename.startswith("monitor_") and filename.endswith(".json"):
        try:
            import storage as _storage
            _storage.save(filename, data)
            return
        except Exception:
            pass
    with open(path, "w") as f:
        json.dump(data, f)


# ─── 1. ЦІНИ ──────────────────────────────────────────────────────────────────

def get_prices():
    ids = ",".join(COINS.values())
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
    data = fetch_json(url)

    # Fallback на Kraken якщо CoinGecko не відповів (rate limit)
    if not data:
        data = _get_prices_kraken()

    if not data:
        return "💰 <b>Ціни</b>\n⚠️ Недоступно"

    prev = load_json_file(PRICE_CACHE, default={})
    now_prices = {}
    lines = []

    for symbol, cg_id in COINS.items():
        price    = data.get(cg_id, {}).get("usd")
        change24 = data.get(cg_id, {}).get("usd_24h_change")
        if price is None:
            continue
        now_prices[cg_id] = {"price": price, "ts": int(time.time())}
        old_entry = prev.get(cg_id)
        old_price = old_entry.get("price") if isinstance(old_entry, dict) else old_entry
        if old_price and old_price > 0:
            pct = (price - old_price) / old_price * 100
            arrow = "🟢" if pct > 0 else "🔴"
            sign = "+" if pct > 0 else ""
            ch = f"{sign}{pct:.2f}% від попер."
            # Зберігаємо pct_prev у рядку для _format_prices_visual
            pct_prev_tag = f"  [pct3h:{pct:+.2f}]"
        elif change24 is not None:
            arrow = "🟢" if change24 > 0 else "🔴"
            sign = "+" if change24 > 0 else ""
            ch = f"{sign}{change24:.2f}% за 24г"
            pct_prev_tag = ""
        else:
            arrow = "⚪️"
            ch = "—"
            pct_prev_tag = ""
        lines.append(f"{arrow} <b>{symbol}</b>  <code>${price:,.2f}</code>  <i>{ch}</i>{pct_prev_tag}")

    save_json_file(PRICE_CACHE, now_prices)

    # ── Дописуємо в historical для графіка (1 точка на годину, 30д) ──────────
    try:
        _now_ts = int(time.time())
        _hist = storage.load_price_history()  # {cg_id: [[ts, price], ...]}
        _cutoff = _now_ts - 30 * 86400
        for _sym, _cg_id in COINS.items():
            _price = now_prices.get(_cg_id, {}).get("price")
            if _price is None:
                continue
            _pts = _hist.get(_cg_id, [])
            # Додаємо тільки якщо остання точка >45 хв тому
            if not _pts or (_now_ts - _pts[-1][0]) > 2700:
                _pts.append([_now_ts, _price])
            # Обрізаємо старіші 30д
            _pts = [p for p in _pts if p[0] >= _cutoff]
            _hist[_cg_id] = _pts
        storage.save_price_history(_hist)
    except Exception as _he:
        print(f"[price history] save error: {_he}")

    # ── ETF та S&P 500 через Yahoo Finance ─────────────────────────────────────
    etf_block = _get_etf_prices()
    if etf_block:
        lines.append("")
        lines.append(etf_block)

    return "💹 <b>ЦІНИ АКТИВІВ</b>\n\n" + "\n".join(lines)


def _yahoo_quote(sym: str) -> tuple[float | None, float | None]:
    """Отримує (price, pct_change) через Yahoo Finance v8 API (без yfinance)."""
    import urllib.request, json as _json, ssl
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=2d"
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            d = _json.loads(r.read())
        result = d["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if len(closes) >= 2:
            price = closes[-1]
            prev  = closes[-2]
            pct   = (price - prev) / prev * 100
            return price, pct
        elif len(closes) == 1:
            return closes[-1], None
    except Exception as _e:
        print(f"[yahoo quote {sym}] {_e}")
    return None, None


def _fetch_etf_rows(tickers: list) -> list:
    """Завантажує ціни для списку тікерів, повертає рядки."""
    rows = []
    for name, sym, icon in tickers:
        # Escape HTML-спецсимволів в назві (S&P500 → S&amp;P500)
        name_h = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        try:
            price, pct = _yahoo_quote(sym)
            if price is not None:
                if pct is not None:
                    arrow = "🟢" if pct > 0 else "🔴"
                    sign  = "+" if pct > 0 else ""
                    rows.append(f"{arrow} <b>{name_h}</b>  <code>${price:,.2f}</code>  <i>{sign}{pct:.2f}% за день</i>")
                else:
                    rows.append(f"⚪️ <b>{name_h}</b>  <code>${price:,.2f}</code>")
            else:
                # fallback: yfinance
                try:
                    import yfinance as yf
                    h = yf.Ticker(sym).history(period="2d")
                    if len(h) >= 2:
                        c    = h["Close"].iloc[-1]
                        prev = h["Close"].iloc[-2]
                        pct2 = (c - prev) / prev * 100
                        arrow = "🟢" if pct2 > 0 else "🔴"
                        sign  = "+" if pct2 > 0 else ""
                        rows.append(f"{arrow} <b>{name_h}</b>  <code>${c:,.2f}</code>  <i>{sign}{pct2:.2f}% за день</i>")
                    elif len(h) == 1:
                        rows.append(f"⚪️ <b>{name_h}</b>  <code>${h['Close'].iloc[-1]:,.2f}</code>")
                    else:
                        rows.append(f"⚪️ <b>{name_h}</b>  —")
                except Exception:
                    rows.append(f"⚪️ <b>{name_h}</b>  —")
        except Exception as _e:
            print(f"[etf prices {name}] {_e}")
            rows.append(f"⚪️ <b>{name_h}</b>  —")
    return rows


# 5 тікерів для двогодинного звіту
_ETF_TICKERS_SHORT = [
    ("IBIT",   "IBIT",    "🟠"),
    ("ETHA",   "ETHA",    "🔷"),
    ("VAVA",   "VAVA.SW", "🏔️"),
    ("GAVA",   "GAVA",    "🟣"),
    ("QQQ",    "QQQ",     "💻"),
]

# Повний список для тижневого/місячного звіту
_ETF_TICKERS_FULL = [
    # ETF
    ("IBIT",   "IBIT",    "🟠"),
    ("ETHA",   "ETHA",    "🔷"),
    ("VAVA",   "VAVA.SW", "🏔️"),
    ("GAVA",   "GAVA",    "🟣"),
    ("QQQ",    "QQQ",     "💻"),
    ("SPY",    "SPY",     "📊"),
    # Індекси
    ("S&P500", "^GSPC",   "📈"),
    ("NASDAQ", "^IXIC",   "📉"),
    ("DOW",    "^DJI",    "🏦"),
    # Акції
    ("NVDA",   "NVDA",    "🟩"),
    ("AAPL",   "AAPL",    "🍎"),
    ("MSFT",   "MSFT",    "🪟"),
    ("TSLA",   "TSLA",    "⚡"),
    ("AMZN",   "AMZN",    "📦"),
    ("GOOGL",  "GOOGL",   "🔍"),
    ("META",   "META",    "👁️"),
    ("BRK-B",  "BRK-B",   "💼"),
    ("JPM",    "JPM",     "🏛️"),
    ("COIN",   "COIN",    "🪙"),
]


def _get_etf_prices(full: bool = False) -> str:
    """Повертає рядок з цінами ETF/акцій.
    full=False → 5 тікерів (двогодинний звіт)
    full=True  → повний список (тижневий/місячний)
    """
    tickers = _ETF_TICKERS_FULL if full else _ETF_TICKERS_SHORT
    rows = _fetch_etf_rows(tickers)
    if rows:
        return "📊 <b>ETF / ІНДЕКСИ / АКЦІЇ</b>\n" + "\n".join(rows)
    return ""


def _get_prices_kraken():
    """Fallback: отримує ціни з Kraken (публічний API, без ключа, без блокування)."""
    # Kraken повертає власні назви пар (XXBTZUSD, XETHZUSD тощо)
    KRAKEN_MAP = {
        "bitcoin":      ("XBTUSD",  ["XXBTZUSD", "XBTUSD"]),
        "ethereum":     ("ETHUSD",  ["XETHZUSD", "ETHUSD"]),
        "avalanche-2":  ("AVAXUSD", ["AVAXUSD"]),
        "ondo-finance": ("ONDOUSD", ["ONDOUSD"]),
    }
    try:
        pairs = ",".join(v[0] for v in KRAKEN_MAP.values())
        raw = fetch_json(f"https://api.kraken.com/0/public/Ticker?pair={pairs}")
        if not raw or raw.get("error"):
            return None
        result_data = raw.get("result", {})
        out = {}
        for cg_id, (_, aliases) in KRAKEN_MAP.items():
            item = None
            for alias in aliases:
                if alias in result_data:
                    item = result_data[alias]
                    break
            if not item:
                continue
            price    = float(item["c"][0])
            open24   = float(item["o"])
            change24 = (price - open24) / open24 * 100 if open24 else 0
            out[cg_id] = {"usd": price, "usd_24h_change": round(change24, 2)}
        return out if out else None
    except Exception as e:
        print(f"Kraken fallback error: {e}")
        return None


# ─── 2. ПОГОДА ────────────────────────────────────────────────────────────────

def get_weather():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=48.7163&longitude=21.2611"
        "&current=temperature_2m,apparent_temperature,weathercode,windspeed_10m,precipitation,relative_humidity_2m,surface_pressure"
        "&hourly=temperature_2m,precipitation,precipitation_probability,weathercode,windspeed_10m"
        "&daily=temperature_2m_max,temperature_2m_min,weathercode,sunrise,sunset,uv_index_max,precipitation_sum"
        "&forecast_days=2&timezone=Europe%2FPrague"
    )
    data = fetch_json(url)
    if not data:
        return "🌡 <b>Погода Košice</b>\n⚠️ Недоступно"

    WMO = {
        0: "☀️ Ясно", 1: "🌤 Перев. ясно", 2: "⛅️ Мінлива хмарність", 3: "☁️ Хмарно",
        45: "🌫 Туман", 48: "🌫 Туман",
        51: "🌦 Мряка", 53: "🌦 Мряка", 55: "🌦 Мряка",
        61: "🌧 Дощ", 63: "🌧 Дощ", 65: "🌧 Сильний дощ",
        71: "❄️ Сніг", 73: "❄️ Сніг", 75: "❄️ Сильний сніг",
        80: "🌦 Злива", 81: "🌦 Злива", 82: "⛈ Сильна злива",
        95: "⛈ Гроза", 96: "⛈ Гроза з градом", 99: "⛈ Сильна гроза",
    }
    RAIN  = {51, 53, 55, 61, 63, 65, 80, 81, 82}
    SNOW  = {71, 73, 75, 77, 85, 86}
    STORM = {95, 96, 99}

    current = data.get("current", {})
    temp  = current.get("temperature_2m")
    feel  = current.get("apparent_temperature")
    code  = current.get("weathercode", 0)
    wind  = current.get("windspeed_10m")
    hum   = current.get("relative_humidity_2m")
    desc  = WMO.get(code, "—")

    daily = data.get("daily", {})
    tmax = daily.get("temperature_2m_max", [None])[0]
    tmin = daily.get("temperature_2m_min", [None])[0]
    sunrise = daily.get("sunrise", [""])[0][11:16] if daily.get("sunrise") else "—"
    sunset  = daily.get("sunset",  [""])[0][11:16] if daily.get("sunset")  else "—"
    uv      = daily.get("uv_index_max", [None])[0]
    precip_sum = daily.get("precipitation_sum", [None])[0]

    uv_str = ""
    if uv is not None:
        uv_lvl = "🟢 Низький" if uv < 3 else ("🟡 Помірний" if uv < 6 else ("🟠 Високий" if uv < 8 else "🔴 Дуже високий"))
        uv_str = f"\n• УФ індекс: {uv:.0f} — {uv_lvl}"

    # Сон — додаємо в погодний блок
    sleep_line = ""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from sleep import get_last_night_sleep
        _sl = get_last_night_sleep()
        if _sl:
            sleep_line = f"\n• {_sl}"
    except Exception as _se:
        print(f"sleep error: {_se}")

    result = (
        f"🌤 <b>ПОГОДА — Košice</b>\n"
        f"• {desc}  <b>{temp:.0f}°C</b>  <i>(відч. {feel:.0f}°C)</i>\n"
        f"• 🔻 {tmin:.0f}°C  /  🔺 {tmax:.0f}°C\n"
        f"• 💨 {wind:.0f} км/г   💧 {hum:.0f}%"
        f"{sleep_line}"
    )
    if precip_sum and precip_sum > 0:
        result += f"   🌧 {precip_sum:.1f} мм"
    result += f"\n• 🌅 {sunrise}   🌇 {sunset}"
    if uv_str:
        result += uv_str

    # Прогноз по годинах (наступні 6г)
    hourly = data.get("hourly", {})
    times  = hourly.get("time", [])
    h_temps = hourly.get("temperature_2m", [])
    h_codes = hourly.get("weathercode", [])
    h_probs = hourly.get("precipitation_probability", [])
    h_winds = hourly.get("windspeed_10m", [])

    local_hour = (datetime.now(timezone.utc).hour + 2) % 24
    forecast_lines = []
    for i, t in enumerate(times):
        try:
            h = int(t[11:13])
        except:
            continue
        diff = (h - local_hour) % 24
        if 1 <= diff <= 6:
            c = h_codes[i] if i < len(h_codes) else 0
            tmp = h_temps[i] if i < len(h_temps) else "—"
            pr = h_probs[i] if i < len(h_probs) else 0
            wd = h_winds[i] if i < len(h_winds) else 0
            icon = WMO.get(c, "—").split()[0]
            rain_str = f"🌧{pr}%" if pr >= 30 else ""
            forecast_lines.append(f"<code>{t[11:16]}</code> {icon}{tmp:.0f}°{rain_str}")

    if forecast_lines:
        result += "\n\n<b>Прогноз:</b>  " + "  │  ".join(forecast_lines[:6])

    # Прогноз на завтра
    tmax_tmr   = daily.get("temperature_2m_max",  [None, None])[1]
    tmin_tmr   = daily.get("temperature_2m_min",  [None, None])[1]
    code_tmr   = daily.get("weathercode",          [0,    0])[1]
    precip_tmr = daily.get("precipitation_sum",   [None, None])[1]
    if tmax_tmr is not None and tmin_tmr is not None:
        desc_tmr = WMO.get(code_tmr, "—")
        rain_tmr = f"  🌧 {precip_tmr:.1f} мм" if precip_tmr and precip_tmr > 0 else ""
        result += (
            f"\n\n<b>Завтра:</b>  {desc_tmr}  "
            f"🔻{tmin_tmr:.0f}°  /  🔺{tmax_tmr:.0f}°{rain_tmr}"
        )
        # Погодинний прогноз на завтра по ключових годинах
        from datetime import date as _date_cls
        tomorrow_str = (_date_cls.today() + __import__('datetime').timedelta(days=1)).strftime("%Y-%m-%d")
        KEY_HOURS = {9, 12, 15, 18, 21}
        tmr_lines = []
        for i, t in enumerate(times):
            if not t.startswith(tomorrow_str):
                continue
            try:
                h = int(t[11:13])
            except:
                continue
            if h not in KEY_HOURS:
                continue
            c = h_codes[i] if i < len(h_codes) else 0
            tmp = h_temps[i] if i < len(h_temps) else None
            pr = h_probs[i] if i < len(h_probs) else 0
            icon = WMO.get(c, "—").split()[0]
            rain_str = f"🌧{pr}%" if pr >= 30 else ""
            tmp_str = f"{tmp:.0f}°" if tmp is not None else "—"
            tmr_lines.append(f"<code>{h:02d}:00</code> {icon}{tmp_str}{rain_str}")
        if tmr_lines:
            result += "\n         " + "  │  ".join(tmr_lines)

    # Попередження
    warnings = []
    for i, t in enumerate(times):
        try:
            h = int(t[11:13])
        except:
            continue
        diff = (h - local_hour) % 24
        if 0 < diff <= 3:
            c = h_codes[i] if i < len(h_codes) else 0
            pr = h_probs[i] if i < len(h_probs) else 0
            if pr >= 60 or c in RAIN | SNOW | STORM:
                kind = "❄️ Сніг" if c in SNOW else ("⛈ Гроза" if c in STORM else "🌧 Дощ")
                warnings.append(f"  {kind} о {t[11:16]} ({pr}%)")
    if warnings:
        result += "\n⚠️ <b>Найближчі 3г:</b>\n" + "\n".join(warnings)

    return result


# ─── 3. КАЛЕНДАР ──────────────────────────────────────────────────────────────

def _get_google_token(creds_data, scope):
    """Отримує access token для service account через JWT — без googleapiclient."""
    import base64, hashlib, hmac, struct, time as _time

    def _b64url(data):
        if isinstance(data, str):
            data = data.encode()
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    now_ts = int(_time.time())
    header  = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}))
    payload = _b64url(json.dumps({
        "iss": creds_data["client_email"],
        "scope": scope,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now_ts,
        "exp": now_ts + 3600,
    }))
    signing_input = f"{header}.{payload}".encode()

    # Підпис через cryptography або fallback через subprocess openssl
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        private_key = serialization.load_pem_private_key(
            creds_data["private_key"].encode(), password=None)
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False, mode="w") as f:
            f.write(creds_data["private_key"])
            pem_path = f.name
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", pem_path],
            input=signing_input, capture_output=True)
        signature = proc.stdout
        import os as _os; _os.unlink(pem_path)

    jwt_token = f"{header}.{payload}.{_b64url(signature)}"

    if _HAS_REQUESTS:
        resp = _requests.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_token,
        }, timeout=15)
        resp.raise_for_status()
        return resp.json()["access_token"]
    else:
        import urllib.parse
        body = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_token,
        }).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token",
            data=body, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())["access_token"]


def _get_all_calendar_ids(headers):
    """Повертає список всіх calendar_id з Google Calendar (всі підписані календарі)."""
    try:
        url = "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=50"
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            items = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                items = json.loads(r.read()).get("items", [])
        ids = [it["id"] for it in items if not it.get("deleted")]
        # debug removed
        return ids
    except Exception as e:
        print(f"_get_all_calendar_ids error: {e}")
        return ["novosadovoleg@gmail.com"]


def _fetch_events_all_calendars(headers, t_min, t_max, max_per_cal=20):
    """Збирає події з УСІХ календарів (включно з нагадуваннями, завданнями, ДН)."""
    cal_ids = _get_all_calendar_ids(headers)
    all_events = []
    seen = set()
    for cal_id in cal_ids:
        try:
            url = (
                f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(cal_id, safe='')}/events"
                f"?timeMin={urllib.parse.quote(t_min.isoformat())}"
                f"&timeMax={urllib.parse.quote(t_max.isoformat())}"
                f"&singleEvents=true&orderBy=startTime&maxResults={max_per_cal}"
            )
            if _HAS_REQUESTS:
                r = _requests.get(url, headers=headers, timeout=10)
                if r.status_code != 200:
                    print(f"Calendar API error: cal={cal_id} status={r.status_code}")
                    continue
                events = r.json().get("items", [])
            else:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as r:
                    events = json.loads(r.read()).get("items", [])
            for ev in events:
                uid = ev.get("id", "")
                if uid not in seen:
                    seen.add(uid)
                    ev["_cal_id"] = cal_id
                    all_events.append(ev)
        except Exception as e:
            print(f"_fetch_events_all_calendars cal={cal_id} error: {e}")
    # Сортуємо по часу початку
    def _sort_key(ev):
        s = ev["start"].get("dateTime") or ev["start"].get("date", "")
        return s
    all_events.sort(key=_sort_key)
    return all_events


def _calendar_access_token():
    """Отримує Calendar access token — спочатку через Gmail OAuth2 refresh token,
    потім fallback на service account."""
    token = _gmail_access_token()
    if token:
        return token
    # fallback: service account
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if creds_json:
        try:
            return _get_google_token(
                json.loads(creds_json),
                "https://www.googleapis.com/auth/calendar.readonly")
        except Exception as e:
            print(f"Calendar service account token error: {e}")
    return None


def get_calendar():
    now = datetime.now(timezone.utc)
    tz_local_top   = timezone(timedelta(hours=2))
    now_local_top  = now.astimezone(tz_local_top)
    date_today    = now_local_top.strftime("%d.%m.%Y")
    date_tomorrow = (now_local_top + timedelta(hours=24)).strftime("%d.%m.%Y")

    token = _calendar_access_token()
    if not token:
        return "📅 <b>Календар</b>\n⚠️ Не налаштовано"

    try:
        headers = {"Authorization": f"Bearer {token}"}

        # Часові межі (Košice UTC+2)
        tz_local = timezone(timedelta(hours=2))
        now_local = now.astimezone(tz_local)
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        today_end      = today_start + timedelta(hours=24)
        tomorrow_start = today_end
        tomorrow_end   = tomorrow_start + timedelta(hours=24)

        # Читаємо ВСІ календарі
        today_events    = _fetch_events_all_calendars(headers, today_start, today_end)
        tomorrow_events = _fetch_events_all_calendars(headers, tomorrow_start, tomorrow_end)

        def format_events(events):
            lines = []
            for ev in events:
                start   = ev["start"].get("dateTime") or ev["start"].get("date")
                summary = ev.get("summary", "(без назви)")
                try:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    t  = dt.astimezone(timezone(timedelta(hours=2))).strftime("%H:%M") if "T" in start else "весь день"
                except Exception:
                    t = start
                lines.append(f"• {t} — <b>{esc(summary)}</b>")
            return lines

        result  = "📅 <b>КАЛЕНДАР</b>\n"
        result += f"<b>Сьогодні {date_today}:</b>\n"
        today_lines = format_events(today_events)
        result += "\n".join(today_lines) if today_lines else "Нічого не заплановано"

        result += f"\n\n<b>Завтра {date_tomorrow}:</b>\n"
        tomorrow_lines = format_events(tomorrow_events)
        result += "\n".join(tomorrow_lines) if tomorrow_lines else "Нічого не заплановано"

        return result

    except Exception as e:
        return f"📅 <b>Календар</b>\n⚠️ Помилка: {esc(str(e)[:120])}"


# ─── 4. EMAIL (Gmail API) ────────────────────────────────────────────────────

def decode_header_str(h):
    parts = email.header.decode_header(h or "")
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def is_spam(sender, subject):
    s, sub = sender.lower(), subject.lower()
    return any(x in s for x in IGNORE_SENDERS) or any(x in sub for x in IGNORE_SUBJECTS)


def _gmail_access_token():
    """Отримує Gmail access token через refresh token."""
    client_id     = os.environ.get("GMAIL_CLIENT_ID", "878341164164-mm5q8t2kuk26dj44prkjvl1k27q15026.apps.googleusercontent.com")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "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")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "1//06mfG58ga3PC6CgYIARAAGAYSNwF-L9IrEkVK67K4DHR4Dj2icDG1OA2q1BlKaRUeJHBv49mbgbhA8SaZCdpBmClcnEtFnGUsCkE")
    if not all([client_id, client_secret, refresh_token]):
        return None
    try:
        if _HAS_REQUESTS:
            r = _requests.post("https://oauth2.googleapis.com/token", data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            }, timeout=15)
            r.raise_for_status()
            return r.json().get("access_token")
        else:
            body = urllib.parse.urlencode({
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            }).encode()
            req = urllib.request.Request("https://oauth2.googleapis.com/token",
                data=body, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()).get("access_token")
    except Exception as e:
        print(f"Gmail token error: {e}")
        return None


def _gmail_list(token, label_ids, max_results=10, q=""):
    """Повертає список {id, threadId} повідомлень."""
    params = {"maxResults": max_results, "labelIds": label_ids}
    if q:
        params["q"] = q
    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urllib.parse.urlencode(
        [("labelIds", lid) for lid in label_ids] +
        ([("q", q)] if q else []) +
        [("maxResults", max_results)]
    )
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            return r.json().get("messages", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()).get("messages", [])
    except Exception as e:
        print(f"Gmail list error ({label_ids}): {e}")
        return []


def _gmail_get(token, msg_id, fmt="metadata"):
    """Отримує один лист. fmt='metadata' або 'full'."""
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format={fmt}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            return r.json()
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
    except Exception as e:
        print(f"Gmail get error ({msg_id}): {e}")
        return None


def _extract_header(msg_data, name):
    for h in msg_data.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _extract_body_preview(msg_data, max_chars=120):
    """Витягує preview з Gmail API повідомлення (format=full)."""
    try:
        import html as _html

        def get_parts(payload):
            parts = []
            if payload.get("mimeType", "").startswith("multipart"):
                for p in payload.get("parts", []):
                    parts.extend(get_parts(p))
            else:
                parts.append(payload)
            return parts

        payload = msg_data.get("payload", {})
        all_parts = get_parts(payload)

        body = ""
        # Спочатку шукаємо text/plain
        for p in all_parts:
            if p.get("mimeType") == "text/plain":
                data = p.get("body", {}).get("data", "")
                if data:
                    body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                    break

        # Якщо немає — беремо text/html і конвертуємо в текст
        if not body:
            for p in all_parts:
                if p.get("mimeType") == "text/html":
                    data = p.get("body", {}).get("data", "")
                    if data:
                        html_body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                        # Видаляємо style/script блоки повністю
                        html_body = re.sub(r'<style[^>]*>.*?</style>', ' ', html_body, flags=re.DOTALL | re.IGNORECASE)
                        html_body = re.sub(r'<script[^>]*>.*?</script>', ' ', html_body, flags=re.DOTALL | re.IGNORECASE)
                        # Заміняємо теги на пробіли
                        body = re.sub(r'<[^>]+>', ' ', html_body)
                        break

        body = _html.unescape(body)
        body = re.sub(r'https?://\S+', '', body)
        body = re.sub(r'\{[^}]*\}', '', body)   # CSS блоки типу {color: red}
        body = re.sub(r'@[a-zA-Z-]+\s*\{[^}]*\}', '', body)  # @media etc
        body = re.sub(r'\[.*?\]', '', body)
        body = re.sub(r'(unsubscribe|відписатись|view in browser|view this post|click here).{0,60}', '', body, flags=re.IGNORECASE)
        body = re.sub(r'\s+', ' ', body).strip()

        if len(body) > max_chars:
            body = body[:max_chars].rsplit(' ', 1)[0] + "…"
        return body if body else "—"
    except:
        return "—"


def _parse_gmail_msg(msg_data, full=False):
    """Повертає (subject, sender_clean, preview, is_unread)."""
    subject = decode_header_str(_extract_header(msg_data, "Subject")) or "(no subject)"
    sender  = decode_header_str(_extract_header(msg_data, "From")) or ""
    sender_clean = re.sub(r'<.*?>', '', sender).strip().strip('"') or sender
    is_unread = "UNREAD" in msg_data.get("labelIds", [])
    preview = _extract_body_preview(msg_data) if full else (msg_data.get("snippet", "") or "—")
    if len(preview) > 120:
        preview = preview[:120].rsplit(' ', 1)[0] + "…"
    return subject, sender_clean, preview, is_unread


def _gemini_summarize(text, max_input=3000):
    """Робить короткий actionable summary через Gemini API."""
    api_key = os.environ.get("GEMINI_API_KEY", "AIzaSyDRXcGERTNILIEDKbmgTKSXUuiwt1oKeGM")
    if not api_key or not text or text == "—":
        return None
    try:
        text_trimmed = text[:max_input]
        prompt = (
            "Прочитай цей email і дай ДУЖЕ короткий опис (1 речення українською, макс 120 символів). "
            "Формат: якщо потрібна дія — почни з емодзі дії (⚠️ помилка, 📋 інфо, 💰 фінанси, ✅ підтвердження, 📩 відповідь потрібна). "
            "Тільки суть і що робити. Без 'Лист про', без 'Повідомлення про'.\n\nЛист:\n" + text_trimmed
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        summary = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return summary[:200]
    except Exception as e:
        print(f"Gemini summary error: {e}")
        return None


def format_email_item(subject, sender, preview, is_unread=False, ai_summary=None, ai_analysis=None, uid_str=None):
    """
    У звіті — тільки від кого і тема. Опис — окремо по кнопці.
    uid_str: якщо передано — додає кнопки під листом
    """
    status = "🔴 <b>НОВЕ</b>" if is_unread else "✉️"
    # Класифікація по sender/subject
    s_low = subject.lower() + sender.lower()
    if any(k in s_low for k in ["invoice", "інвойс", "рахунок", "payment", "оплат"]):
        cat = "💰"
    elif any(k in s_low for k in ["security", "безпек", "password", "пароль", "alert", "verify"]):
        cat = "🔐"
    elif any(k in s_low for k in ["order", "замовлен", "delivery", "доставк", "shipment"]):
        cat = "📦"
    elif any(k in s_low for k in ["meeting", "зустріч", "calendar", "invite", "запрошен"]):
        cat = "📅"
    elif any(k in s_low for k in ["job", "робот", "vacancy", "вакансі", "career"]):
        cat = "💼"
    else:
        cat = "📩"

    lines = [
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        f"{cat} {status}",
        f"📌 <b>{esc(subject[:60])}</b>",
        f"👤 {esc(sender[:50])}",
    ]

    return "\n".join(lines)


def _imap_connect():
    """Підключення до Gmail через IMAP."""
    import socket as _s
    _s.setdefaulttimeout(20)
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "zbzlkvxjspuekbuk")
    mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=20)
    mail.login(GMAIL_USER, app_password)
    return mail

def _imap_decode_header(raw):
    """Декодує email заголовок."""
    parts = email.header.decode_header(raw or "")
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(enc or "utf-8", errors="replace"))
            except Exception:
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)

def _imap_get_body(msg):
    """Витягує текст листа (plain або з HTML)."""
    import re as _re
    body = ""
    if msg.is_multipart():
        # Спочатку шукаємо text/plain
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                try:
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace")
                    break
                except Exception:
                    pass
        # Якщо plain не знайшли — беремо HTML і стрипаємо теги
        if not body.strip():
            for part in msg.walk():
                ct = part.get_content_type()
                cd = str(part.get("Content-Disposition", ""))
                if ct == "text/html" and "attachment" not in cd:
                    try:
                        html = part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", errors="replace")
                        body = _re.sub(r'<[^>]+>', ' ', html)
                        body = _re.sub(r'\s+', ' ', body).strip()
                        break
                    except Exception:
                        pass
    else:
        try:
            raw = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                body = _re.sub(r'<[^>]+>', ' ', raw)
                body = _re.sub(r'\s+', ' ', body).strip()
            else:
                body = raw
        except Exception:
            pass
    return body[:3000]

def get_emails():
    try:
        mail = _imap_connect()
        mail.select("INBOX")

        # UID-based пошук (правильно — sequence numbers не persistent між сесіями)
        # Беремо: непрочитані primary + останні 15 прочитаних primary
        _, p_unseen = mail.uid('search', None, 'X-GM-RAW "category:primary is:unread"')
        _, p_all    = mail.uid('search', None, 'X-GM-RAW "category:primary"')

        primary_unread_uids = set(u.decode() for u in p_unseen[0].split())
        primary_all_uids    = [u.decode() for u in p_all[0].split()]

        # Якщо primary порожній — беремо всі UNSEEN як fallback
        if not primary_all_uids:
            _, fallback = mail.uid('search', None, 'UNSEEN')
            primary_all_uids = [u.decode() for u in fallback[0].split()]
            primary_unread_uids = set(primary_all_uids)

        # Об'єднуємо: всі непрочитані + останні 15 прочитаних, від нових до старих
        combined = list(dict.fromkeys(
            list(primary_unread_uids) + primary_all_uids[-15:]
        ))
        combined = sorted(combined, key=lambda x: int(x))[::-1]

        # Мінімальний чорний список — тільки явні системні нотифікації
        # (YouTube, Duolingo, Maps тощо що Gmail іноді кладе в Primary)
        _ALWAYS_SKIP = {
            "noreply@youtube.com", "no-reply@youtube.com",
            "no-reply@accounts.google.com", "noreply-maps-timeline@google.com",
            "hello@duolingo.com", "no-reply@duolingo.com",
            "no-reply@medium.com",
        }

        primary = []

        for uid in combined:
            if len(primary) >= 7:
                break
            _, msg_data = mail.uid('fetch', uid.encode(), "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject  = _imap_decode_header(msg.get("Subject", "(без теми)"))
            sender   = _imap_decode_header(msg.get("From", ""))
            is_unread = uid in primary_unread_uids

            # Пропускаємо тільки явні системні нотифікації
            email_match = re.search(r'[\w.+%-]+@[\w.-]+\.[a-z]{2,}', sender.lower())
            email_addr = email_match.group(0) if email_match else ""
            if email_addr in _ALWAYS_SKIP:
                continue

            # Зберігаємо UID + тіло листа
            primary.append((subject, sender, uid, is_unread, _imap_get_body(msg)))

        mail.logout()

        unread_count = sum(1 for _, _, _, u, _ in primary if u)

        # Заголовок блоку
        if unread_count > 0:
            header = f"📬 <b>ПОШТА</b>  🔴 {unread_count} непрочитаних"
        else:
            header = f"📬 <b>ПОШТА</b>"

        if not primary:
            return header + "\n\n✅ Нових листів немає"

        # Повертаємо спеціальний dict щоб main() міг надіслати кожен лист з кнопками
        items = []
        email_cache = {}
        for s, snd, uid_s, u, body_text in primary:
            items.append({
                "subject": s,
                "sender": snd,
                "uid": uid_s,
                "unread": u,
            })
            # Кешуємо тіло для кнопки "Описати"
            email_cache[uid_s] = {
                "subject": s,
                "sender": snd,
                "body": body_text or "",
            }

        # Зберігаємо кеш в GitHub storage
        try:
            storage.save("email_body_cache.json", email_cache)
            print(f"[get_emails] body cache saved, {len(email_cache)} items", flush=True)
        except Exception as _ce:
            print(f"[get_emails] cache save error: {_ce}", flush=True)

        return {"__email_block__": True, "header": header, "items": items}

    except Exception as e:
        print(f"get_emails IMAP error: {e}")
        return f"📬 <b>Email</b>\n⚠️ Помилка: {e}"


# ─── 4b. МИТТЄВІ СПОВІЩЕННЯ ПРО НОВІ ЛИСТИ ───────────────────────────────────

ALERT_EMAIL_FILE = os.path.join(_DATA_DIR, "monitor_alert_emails.json")

_SKIP_EMAILS = {
    "noreply@youtube.com", "no-reply@youtube.com",
    "no-reply@accounts.google.com", "noreply-maps-timeline@google.com",
    "hello@duolingo.com", "no-reply@duolingo.com", "no-reply@medium.com",
    "noreply@tradingview.com",
}

# In-memory dedup — захист від дублів в межах одного процесу
_EMAIL_SENT_INMEM: set = set()

def _email_sent_ids():
    """Повертає set вже надісланих IMAP UID (з GitHub data branch — persistent, без кешу)."""
    # Використовуємо storage.py (hardcoded token + data branch) — той самий шлях що й _email_save_ids
    try:
        import storage as _st
        from storage import invalidate_cache
        invalidate_cache("monitor_alert_emails.json")
        data = _st.load("monitor_alert_emails.json", default={})
        return set(str(x) for x in data.get("sent_ids", []))
    except Exception as e:
        print(f"_email_sent_ids error: {e}")
        return set()

def _email_save_ids(sent_ids: set):
    """Зберігає sent UID в GitHub. Тримає останні 1000."""
    try:
        import storage as _st
        lst = sorted(int(x) for x in sent_ids if str(x).isdigit())[-1000:]
        _st.save("monitor_alert_emails.json", {"sent_ids": [str(x) for x in lst]})
    except Exception as e:
        print(f"_email_save_ids error: {e}")

def _gemini_email_analysis(full_text: str) -> dict:
    """Аналізує лист через Gemini: детальний переказ + думка."""
    import re as _re
    api_key = os.environ.get("GEMINI_API_KEY", "AIzaSyDRXcGERTNILIEDKbmgTKSXUuiwt1oKeGM")

    prompt = (
        "Проаналізуй цей email. Відповідь — ТІЛЬКИ валідний JSON, без markdown, без коментарів:\n"
        '{"description": "...", "opinion": "..."}\n\n'
        "description: переказ змісту — про що лист, ключові цифри/дати/суми, що очікується від одержувача (2-4 речення українською).\n"
        "opinion: твоя коротка думка — чи реагувати і що зробити (1 речення українською).\n\n"
        f"Лист:\n{full_text[:2000]}"
    )
    req_body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.3}
    }).encode()

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=req_body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            resp_data = json.loads(r.read())
        raw = resp_data["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Прибираємо markdown огорожі якщо є
        raw = _re.sub(r"^```(?:json)?\s*", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"\s*```\s*$", "", raw, flags=_re.MULTILINE)
        raw = raw.strip()
        # Пряме парсування
        try:
            result = json.loads(raw)
            if isinstance(result, dict) and "description" in result:
                return result
        except Exception:
            pass
        # Regex fallback — витягти description/opinion вручну
        desc_m = _re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, _re.DOTALL)
        opin_m = _re.search(r'"opinion"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, _re.DOTALL)
        if desc_m:
            return {
                "description": desc_m.group(1),
                "opinion": opin_m.group(1) if opin_m else ""
            }
        print(f"[email AI] parse failed, raw: {raw[:150]}")
        return None
    except Exception as e:
        print(f"[email AI] error: {type(e).__name__}: {e}")
        return None

def _send_telegram_gif_only():
    """Надсилає тільки GIF без тексту."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendAnimation"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "animation": "https://storage.googleapis.com/runable-templates/cli-uploads%2F1zsprqn6ymqOFgAJnNEK2HbTycMPBvLc%2F84VzoRtuRjk0i6Ju6EUAd%2Fmail_alert.gif",
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        print(f"_send_telegram_gif_only error: {e}")
        return False


def _send_telegram_text_with_keyboard(text: str, keyboard: dict):
    """Надсилає текстове повідомлення з inline keyboard."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text[:4096],
        "parse_mode": "HTML",
        "reply_markup": keyboard
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
            if not resp.get("ok"):
                print(f"[tg] sendMessage error: {resp}")
                return False
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[tg] sendMessage HTTP {e.code}: {body}")
        return False
    except Exception as e:
        print(f"_send_telegram_text_with_keyboard error: {e}")
        return False


def _imap_delete_email(uid_str: str):
    """Видаляє лист з Gmail по UID через IMAP."""
    try:
        mail = _imap_connect()
        mail.select("INBOX")
        mail.uid('store', uid_str.encode(), '+FLAGS', '\\Deleted')
        mail.expunge()
        mail.logout()
        return True
    except Exception as e:
        print(f"_imap_delete_email error: {e}")
        return False


def check_new_emails():
    """Перевіряє непрочитані Primary листи — шле сповіщення ОДИН РАЗ на кожен лист (dedup по UID)."""
    try:
        mail = _imap_connect()
        mail.select("INBOX")

        # UID-based пошук (sequence numbers не persistent між IMAP сесіями!)
        _, data = mail.uid('search', None, 'X-GM-RAW "category:primary is:unread"')
        all_unread = data[0].split()

        if not all_unread:
            mail.logout()
            return

        # Завантажуємо вже надіслані з GitHub + in-memory
        sent_ids = _email_sent_ids()
        sent_ids.update(_EMAIL_SENT_INMEM)  # додаємо in-memory dedup

        # Фільтруємо тільки нові (не бачені) — беремо останні 20
        new_uids = [u for u in all_unread[-20:] if u.decode() not in sent_ids]

        if not new_uids:
            mail.logout()
            return

        to_alert = []
        newly_seen = set()

        for uid in new_uids:
            uid_str = uid.decode()
            _, msg_data = mail.uid('fetch', uid, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])

            subject = _imap_decode_header(msg.get("Subject", "(без теми)"))
            sender  = _imap_decode_header(msg.get("From", ""))
            body    = _imap_get_body(msg)

            em = re.search(r'[\w.+%-]+@[\w.-]+\.[a-z]{2,}', sender.lower())
            ea = em.group(0) if em else ""

            # Одразу в in-memory щоб race condition не дублював
            _EMAIL_SENT_INMEM.add(uid_str)
            newly_seen.add(uid_str)

            if ea not in _SKIP_EMAILS:
                category = _classify_email(sender, subject)
                if category in ("spam", "promo"):
                    print(f"[email] skip {category}: {sender[:50]} / {subject[:40]}")
                else:
                    to_alert.append((uid_str, subject, sender, body, category))

        mail.logout()

        # Зберігаємо в GitHub одразу (до надсилання Telegram) — щоб redeploy не дублював
        if newly_seen:
            sent_ids.update(newly_seen)
            _email_save_ids(sent_ids)

        # Сортуємо: 'real' першими
        to_alert.sort(key=lambda x: 0 if x[4] == "real" else 1)

        for uid_str, subject, sender, body, category in to_alert:
            # AI аналіз листа
            full_text = f"Від: {sender}\nТема: {subject}\n\n{body}"
            print(f"[email] analyzing uid={uid_str} subject={subject[:40]}")
            ai = _gemini_email_analysis(full_text)
            if not ai:
                print(f"[email] AI returned None for uid={uid_str}")

            # 1. GIF окремо (без тексту)
            _send_telegram_gif_only()

            # 2. Текст з AI аналізом + кнопки окремим повідомленням
            text = (
                f"📩 <b>━━ НОВИЙ ЛИСТ ━━</b>\n\n"
                f"👤 <b>Від:</b> {esc(sender[:60])}\n"
                f"📋 <b>Тема:</b> {esc(subject[:70])}\n"
            )
            if ai:
                description = ai.get('description', ai.get('summary', '')).strip()
                opinion = ai.get('opinion', '').strip()
                if description:
                    text += f"\n📝 <b>Опис:</b> {esc(description)}\n"
                if opinion:
                    text += f"\n🤖 <b>Моя думка:</b> {esc(opinion)}"
            else:
                # Fallback — тіло листа перші 300 символів
                preview = body[:300].strip() if body else ""
                if preview:
                    text += f"\n📄 <b>Початок:</b> <i>{esc(preview)}...</i>"

            keyboard = {"inline_keyboard": [
                [
                    {"text": "✍️ Відповісти", "callback_data": f"email_reply_{uid_str}"},
                    {"text": "⭐ Важливий",   "callback_data": f"email_star_{uid_str}"},
                ],
                [
                    {"text": "📅 В календар", "callback_data": f"email_cal_{uid_str}"},
                    {"text": "📥 Залишити",   "callback_data": f"email_keep_{uid_str}"},
                    {"text": "🗑 Видалити",   "callback_data": f"email_delete_{uid_str}"},
                ]
            ]}

            _send_telegram_text_with_keyboard(text, keyboard)
            print(f"[email] alert sent: uid={uid_str} subject={subject[:50]}")

    except Exception as e:
        print(f"check_new_emails error: {e}")


# ─── 4c. ПОГОДНІ АЛЕРТИ ───────────────────────────────────────────────────────

WEATHER_ALERT_FILE = os.path.join(_DATA_DIR, "monitor_weather_alert.json")

def check_calendar_reminders():
    """
    Перевіряє всі події з УСІХ календарів.
    За 30 хвилин до кожної події надсилає нагадування (один раз).
    Також нагадує про події 'весь день' о 08:00.
    """
    try:
        token = _calendar_access_token()
        if not token:
            return
        headers = {"Authorization": f"Bearer {token}"}

        now_utc = datetime.now(timezone.utc)
        now_loc = now_utc + timedelta(hours=2)
        today_start = now_loc.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        today_end   = today_start + timedelta(hours=24)

        events = _fetch_events_all_calendars(headers, today_start, today_end, max_per_cal=25)

        # Завантажуємо вже надіслані нагадування
        sent_file = os.path.join(_DATA_DIR, "cal_reminders_sent.json")
        try:
            with open(sent_file) as f:
                sent_reminders = json.load(f)
        except:
            sent_reminders = {}

        # Чистимо старі записи (старіші за 2 дні)
        today_str = now_loc.strftime("%Y-%m-%d")
        sent_reminders = {k: v for k, v in sent_reminders.items() if k >= today_str[:8]}

        changed = False

        for ev in events:
            uid     = ev.get("id", "")
            summary = ev.get("summary", "(без назви)")
            start   = ev["start"].get("dateTime") or ev["start"].get("date")
            is_allday = "T" not in start

            if is_allday:
                # Подія весь день — нагадуємо о 08:00
                key = f"allday_{today_str}_{uid}"
                if not sent_reminders.get(key) and now_loc.hour == 8 and now_loc.minute <= 4:
                    sent_reminders[key] = True
                    changed = True
                    api("sendMessage", {
                        "chat_id": TELEGRAM_CHAT,
                        "text": f"📅 <b>Сьогодні весь день:</b>\n\n📌 {esc(summary)}",
                        "parse_mode": "HTML"
                    })
            else:
                # Подія з часом — нагадуємо за 30 хвилин
                try:
                    dt_event = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    dt_loc   = dt_event + timedelta(hours=2)
                    minutes_left = int((dt_event - now_utc).total_seconds() / 60)
                    key = f"30min_{today_str}_{uid}"
                    if not sent_reminders.get(key) and 28 <= minutes_left <= 32:
                        sent_reminders[key] = True
                        changed = True
                        t_str = dt_loc.strftime("%H:%M")
                        api("sendMessage", {
                            "chat_id": TELEGRAM_CHAT,
                            "text": (
                                f"⏰ <b>Через 30 хвилин:</b>\n\n"
                                f"📌 {esc(summary)}\n"
                                f"🕐 о {t_str}"
                            ),
                            "parse_mode": "HTML"
                        })
                except Exception as e:
                    print(f"check_calendar_reminders ev parse error: {e}")

        if changed:
            with open(sent_file, "w") as f:
                json.dump(sent_reminders, f)

    except Exception as e:
        print(f"check_calendar_reminders error: {e}")


def check_weather_alert():
    """
    Щовечора (~20:00 місцевого) перевіряє погоду на завтра.
    Якщо очікується дощ/гроза/сніг — шле сповіщення.
    Також: якщо зараз різка зміна погоди (>5° за 3г) — миттєве сповіщення.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)

    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=48.7163&longitude=21.2611"
        "&current=temperature_2m,weathercode,precipitation"
        "&daily=weathercode,precipitation_sum,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&forecast_days=2&timezone=Europe%2FPrague"
    )
    data = fetch_json(url)
    if not data:
        return

    state = load_json_file(WEATHER_ALERT_FILE, default={})
    gh_weather, gh_weather_sha = _gh_get_json("monitor_weather_alert.json")
    alerts = []

    WMO_BAD = {
        51, 53, 55, 61, 63, 65, 71, 73, 75, 77,
        80, 81, 82, 85, 86, 95, 96, 99
    }
    WMO_LABEL = {
        51: "🌦 Мряка", 53: "🌦 Мряка", 55: "🌦 Мряка",
        61: "🌧 Дощ", 63: "🌧 Дощ", 65: "🌧 Сильний дощ",
        71: "❄️ Сніг", 73: "❄️ Сніг", 75: "❄️ Сильний сніг",
        80: "🌦 Злива", 81: "🌦 Злива", 82: "⛈ Сильна злива",
        95: "⛈ Гроза", 96: "⛈ Гроза з градом", 99: "⛈ Сильна гроза",
    }

    # ── Вечірній алерт про завтра (шлемо між 19:00-21:00) ──
    if 19 <= now_local.hour < 21:
        today_str = now_local.strftime("%Y-%m-%d")
        last_evening = gh_weather.get("last_evening_alert", state.get("last_evening_alert", ""))
        if last_evening != today_str:
            daily = data.get("daily", {})
            times = daily.get("time", [])
            codes = daily.get("weathercode", [])
            precip = daily.get("precipitation_sum", [])
            precip_prob = daily.get("precipitation_probability_max", [])
            tmax = daily.get("temperature_2m_max", [])
            tmin = daily.get("temperature_2m_min", [])

            # Завтра = індекс 1
            if len(times) > 1:
                code = codes[1] if len(codes) > 1 else 0
                pr   = precip[1] if len(precip) > 1 else 0
                prob = precip_prob[1] if len(precip_prob) > 1 else 0
                hi   = tmax[1] if len(tmax) > 1 else None
                lo   = tmin[1] if len(tmin) > 1 else None
                tomorrow_date = (now_local + timedelta(days=1)).strftime("%d.%m")

                if code in WMO_BAD or prob >= 50:
                    label = WMO_LABEL.get(code, "🌧 Опади")
                    temp_str = f"{lo:.0f}…{hi:.0f}°C" if hi and lo else ""
                    msg = (
                        f"🌦 <b>Погода на завтра ({tomorrow_date})</b>\n"
                        f"{label}"
                        + (f", {prob}% імовірність опадів" if prob else "")
                        + (f", {pr:.1f} мм" if pr > 0 else "")
                        + (f"\n🌡 {temp_str}" if temp_str else "")
                        + "\n\n☔ Не забудь парасольку!"
                    )
                    alerts.append(msg)
                    state["last_evening_alert"] = today_str
                    gh_weather["last_evening_alert"] = today_str
                    _gh_save_json("monitor_weather_alert.json", gh_weather, gh_weather_sha)

    # ── Різка зміна температури (>6° за останні 3г) ──
    current = data.get("current", {})
    temp_now = current.get("temperature_2m")
    if temp_now is not None:
        last_temp = state.get("last_temp")
        last_temp_time = state.get("last_temp_time", "")
        now_str = now_local.strftime("%Y-%m-%d %H")

        if last_temp is not None and last_temp_time != now_str:
            diff = temp_now - last_temp
            if abs(diff) >= 6:
                direction = "впала" if diff < 0 else "піднялась"
                alerts.append(
                    f"🌡 <b>Різка зміна температури!</b>\n"
                    f"Температура {direction} на {abs(diff):.0f}°C за 3г\n"
                    f"Зараз: {temp_now:.0f}°C"
                )

        state["last_temp"] = temp_now
        state["last_temp_time"] = now_str

    save_json_file(WEATHER_ALERT_FILE, state)

    for msg in alerts:
        send_telegram(msg)
        print(f"Weather alert sent: {msg[:60]}")


# ─── 4d. КРИПТО НОВИНИ ────────────────────────────────────────────────────────

CRYPTO_NEWS_FILE = os.path.join(_DATA_DIR, "monitor_crypto_news.json")

def _translate_ua(text):
    """Перекладає текст на українську через Google Translate (без ключа)."""
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = "client=gtx&sl=en&tl=uk&dt=t&q=" + urllib.parse.quote(text)
        data = fetch_json(url + "?" + params)
        if data and data[0]:
            return "".join([s[0] for s in data[0] if s and s[0]])
    except Exception as e:
        print(f"translate error: {e}")
    return text  # fallback — оригінал


def check_crypto_news():
    """
    Раз на 4 години перевіряє топ новини з CoinGecko News.
    Шле нові важливі новини в Telegram.
    """
    state = load_json_file(CRYPTO_NEWS_FILE, default={"sent": [], "last_check": ""})

    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    now_str   = now_local.strftime("%Y-%m-%d %H")
    last      = state.get("last_check", "")

    # Не частіше ніж раз на 4г
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d %H").replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_dt).total_seconds() < 4 * 3600:
            return
    except Exception:
        pass

    # CoinGecko News API (безкоштовно, без ключа)
    data = fetch_json("https://api.coingecko.com/api/v3/news?page=1")

    sent     = set(state.get("sent", []))
    new_news = []

    if data:
        items = data.get("data", [])[:10]
        for item in items:
            nid   = str(item.get("id", ""))
            title = item.get("title", "")
            url_  = item.get("url", "")
            if not nid or nid in sent:
                continue
            sent.add(nid)
            new_news.append((title, url_))

    if new_news:
        lines = []
        for title, url_ in new_news[:5]:
            translated = _translate_ua(title)
            lines.append(f"• <a href='{url_}'>{esc(translated[:100])}</a>")
        msg = "📰 <b>Крипто новини</b>\n" + "\n".join(lines)
        send_telegram(msg)
        print(f"Crypto news sent: {len(new_news)} items")

    state["sent"]       = list(sent)[-300:]
    state["last_check"] = now_str
    save_json_file(CRYPTO_NEWS_FILE, state)

    _check_fear_greed()


def _check_fear_greed():
    """Шле Fear & Greed якщо екстремальне значення (< 20 або > 80)."""
    state = load_json_file(CRYPTO_NEWS_FILE, default={})
    last_fg = state.get("last_fg_date", "")
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
    if last_fg == today:
        return

    data = fetch_json("https://api.alternative.me/fng/?limit=1")
    if not data:
        return

    try:
        value = int(data["data"][0]["value"])
        label = data["data"][0]["value_classification"]
    except Exception:
        return

    if value <= 20:
        emoji = "😱"
        msg = f"{emoji} <b>Fear &amp; Greed: {value} — {esc(label)}</b>\nРинок в екстремальному страху. Можливо час купувати?"
    elif value >= 80:
        emoji = "🤑"
        msg = f"{emoji} <b>Fear &amp; Greed: {value} — {esc(label)}</b>\nРинок в екстремальній жадібності. Будь обережний."
    else:
        return  # Нормальне значення — не шлемо

    send_telegram(msg)
    state["last_fg_date"] = today
    save_json_file(CRYPTO_NEWS_FILE, state)


# ─── 5. ПІДСУМОК ТА РЕКОМЕНДАЦІЇ ─────────────────────────────────────────────

def _get_run_recommendation(weather_text):
    """Рекомендація бігти сьогодні."""
    import re as _re
    XML = "/tmp/health_export/apple_health_export/export.xml"
    last_run_days = None
    try:
        with open(XML, "r", encoding="utf-8", errors="replace") as f:
            xml_content = f.read()
        now_utc = datetime.now(timezone.utc)
        run_dates = []
        for line in xml_content.split("\n"):
            if "HKWorkoutActivityTypeRunning" not in line:
                continue
            m2 = _re.search('startDate="([^"]+)"', line)
            if m2:
                s = m2.group(1).strip()
                # parse datetime safely
                import re as re2
                s2 = re2.sub(r" ([+-][0-9]{4})$", r"\1", s).replace(" ", "T", 1)
                try:
                    dt = datetime.fromisoformat(s2).astimezone(timezone.utc)
                    run_dates.append(dt)
                except Exception:
                    pass
        if run_dates:
            last_run_days = (now_utc - max(run_dates)).days
    except Exception as e:
        print(f"run recommendation error: {e}")

    bad_weather = any(x in weather_text.lower() for x in ["гроза", "сильний дощ", "сніг", "злива"])
    if last_run_days is None:
        return "🏃 Даних про пробіжки немає — саме час вийти!"
    elif last_run_days == 0:
        return "🏃 Сьогодні вже бігав — молодець! 💪"
    elif last_run_days <= 2:
        return f"🏃 {last_run_days} дн. без бігу — гарний момент вийти!"
    else:
        if bad_weather:
            return f"🏃 {last_run_days} днів без бігу... Погода не дуже, але ліньки гірше 😄"
        return f"🏃 <b>{last_run_days} днів без бігу!</b> Сьогодні — обов\'язково! 💨"


def _get_current_shift_context(calendar_text=""):
    """Визначає поточний статус зміни Олега.
    Повертає dict: {shift, is_working_now, greeting_override}
    shift: 'night' | 'after_night' | 'early' | 'free'
    is_working_now: True якщо зараз він на роботі
    greeting_override: str або None (якщо потрібне особливе привітання)
    """
    try:
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        shift = shift_info.get("today", "free")
    except Exception:
        # fallback: парсимо calendar_text
        cl = (calendar_text or "").lower()
        h_now = (datetime.now(timezone.utc) + timedelta(hours=2)).hour
        if "нічна" in cl or "нічн" in cl:
            # якщо вже ранок/день — скоріш за все повернувся з нічної
            shift = "after_night" if h_now >= 6 else "night"
        elif "рання" in cl or "ранн" in cl:
            shift = "early"
        else:
            shift = "free"

    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h = now_local.hour

    is_working_now = False
    if shift == "night" and (h >= 17 or h < 6):
        is_working_now = True
    elif shift == "early" and (6 <= h < 18):
        is_working_now = True

    greeting_override = None
    if shift == "after_night":
        # Повернувся з нічної — весь день режим відновлення
        if h < 10:
            greeting_override = "😴 <b>Після нічної — час спати.</b> Не турбую зайвим, відпочивай."
        elif h < 14:
            greeting_override = "🛋 <b>Після нічної зміни.</b> Відновлення важливіше за активність."
        else:
            greeting_override = "🌤 <b>Після нічної — прокидаєшся.</b> Плавний старт дня."
    elif shift == "night":
        if h >= 17 or h < 6:
            greeting_override = "🌙 <b>Нічна зміна.</b> Олег зараз на роботі — тримайся!"
        elif 6 <= h < 10:
            greeting_override = "😴 <b>Після нічної зміни.</b> Час відпочити — заслужено!"
        elif 10 <= h < 16:
            greeting_override = "☀️ <b>Підготовка до нічної.</b> Зміна ввечері — плануй відповідно."
    elif shift == "early":
        if 6 <= h < 18:
            greeting_override = "🏭 <b>Рання зміна.</b> Олег на роботі — вперед!"
        elif h >= 18:
            greeting_override = "🌆 <b>Після ранньої зміни.</b> Відпочинок заслужений."

    return {"shift": shift, "is_working_now": is_working_now, "greeting_override": greeting_override}


def get_summary(prices_text, weather_text, calendar_text, email_text=None, astro_text=None):
    """
    Живий підсумок дня — розділений по темах, з аналізом попередніх підсумків.
    """
    import re as _re, hashlib as _hsh
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h = now_local.hour
    today_str_s = now_local.strftime("%Y-%m-%d")
    # Seed змінюється кожен звіт — з точного часу + хешу переданих даних
    import hashlib as _hsh_seed
    _raw_seed = (prices_text or "") + (calendar_text or "") + (email_text or "") + now_local.strftime("%Y-%m-%d-%H-%M")
    slot_seed = _hsh_seed.md5(_raw_seed.encode()).hexdigest()[:12]

    shift_ctx = _get_current_shift_context(calendar_text)
    shift = shift_ctx.get("shift", "free")

    # ── Збираємо реальні дані для контексту ──────────────────────────────────

    # Погода
    weather_facts = ""
    if weather_text:
        temp_m = _re.search(r"([-−]?\d+)[°℃]", weather_text)
        feels_m = _re.search(r"(?:відчув|feels)[^\d]*([-−]?\d+)", weather_text, _re.I)
        if temp_m:
            weather_facts = f"Погода: {temp_m.group(1)}°C"
            if feels_m:
                weather_facts += f" (відчув. {feels_m.group(1)}°)"
            wl = weather_text.lower()
            if "дощ" in wl: weather_facts += ", дощ"
            elif "сніг" in wl: weather_facts += ", сніг"
            elif "гроза" in wl: weather_facts += ", гроза"
            elif "ясно" in wl or "сонячно" in wl: weather_facts += ", ясно"
            elif "хмарно" in wl: weather_facts += ", хмарно"

    # Крипто
    crypto_facts = ""
    if prices_text:
        coins_data = []
        for coin in ["BTC", "ETH", "AVAX", "ONDO"]:
            row_m = _re.search(r"[^\n]*" + coin + r"[^\n]*", prices_text)
            if not row_m: continue
            row = row_m.group(0)
            price_m = _re.search(r"\$([\d,]+(?:\.\d+)?)", row)
            pct_m = _re.search(r"([+\-−][\d.]+)%", row)
            if price_m:
                pct_str = pct_m.group(0) if pct_m else ""
                trend = "▲" if "🔺" in row else ("▼" if "🔻" in row else "→")
                coins_data.append(f"{coin} ${price_m.group(1)} {pct_str} {trend}")
        up = prices_text.count("🔺")
        dn = prices_text.count("🔻")
        mood = "бичачий" if up > dn else ("ведмежий" if dn > up else "нейтральний")
        crypto_facts = f"Ринок {mood}. " + ", ".join(coins_data)

    # Вага
    weight_facts = ""
    weight_trend = ""
    try:
        wd = storage.load_weight()
        if wd:
            keys = sorted(wd.keys())
            last_key = keys[-1]
            last_w = wd[last_key]
            diff = round(last_w - 78.0, 1)
            # Показуємо останні 7 записів з датами (прочерк якщо нема)
            _w_rows = []
            _check_days = 7
            for _di in range(_check_days - 1, -1, -1):
                _dk = (now_local - timedelta(days=_di)).strftime("%Y-%m-%d")
                _dv = wd.get(_dk)
                _row = f"{_dk}: {_dv} кг" if _dv else f"{_dk}: —"
                _w_rows.append(_row)
            weight_facts = f"Остання: {last_w} кг ({last_key}). Ціль 78 кг — залишилось {diff} кг.\n" + " | ".join(_w_rows)
            if len(keys) >= 7:
                week_ago = wd.get(keys[-7], wd.get(keys[0]))
                delta_week = round(last_w - week_ago, 1)
                weight_trend = f"За тиждень: {'+' if delta_week > 0 else ''}{delta_week} кг"
            if len(keys) >= 30:
                month_ago_val = wd.get(keys[-30], wd.get(keys[0]))
                delta_month = round(last_w - month_ago_val, 1)
                weight_trend += f", за місяць: {'+' if delta_month > 0 else ''}{delta_month} кг"
            if weight_trend:
                weight_facts += f". {weight_trend}"
    except: pass

    # Кроки — з QWatch (основне джерело) або StepsApp
    steps_facts = ""
    try:
        yest = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
        # Спочатку пробуємо QWatch
        _qw_all_s = storage.load("qwatch_data.json", default={})
        _st_today = (_qw_all_s.get(today_str_s) or {}).get("steps", 0) or 0
        _st_yest  = (_qw_all_s.get(yest) or {}).get("steps", 0) or 0
        if _st_today > 0:
            goal_ok = "ціль ✅" if _st_today >= 8000 else f"ціль 8000 {'✅' if _st_today >= 8000 else '❌'}"
            steps_facts = f"Сьогодні: {_st_today:,} кроків — {'✅ ціль' if _st_today >= 8000 else '❌ ціль 8000'}"
        elif _st_yest > 0:
            steps_facts = f"Вчора: {_st_yest:,} кроків — {'✅ ціль' if _st_yest >= 8000 else '❌ ціль 8000'}"
        else:
            # Fallback: StepsApp
            from steps import load_steps_data as _lsd_s
            sd = _lsd_s()
            if sd and sd.get(yest):
                st = sd[yest].get("steps", 0)
                km = sd[yest].get("distance_m", 0) / 1000
                steps_facts = f"Вчора: {st:,} кроків ({km:.1f} км) — {'✅ ціль' if st >= 8000 else '❌ ціль 8000'}"
            else:
                steps_facts = "Дані кроків відсутні (QWatch не синхронізовано?)"
    except: pass

    # Ліки
    meds_facts = ""
    try:
        mdb = storage.load_meds()
        taken = mdb.get(today_str_s)
        if taken is True:
            meds_facts = "Armolopid Plus: прийнято ✅"
        elif taken is False:
            meds_facts = "Armolopid Plus: НЕ прийнято ❌"
        else:
            meds_facts = "Armolopid Plus: ще не відмічено"
    except: pass

    # Звички
    habits_facts = ""
    try:
        hd = storage.load_habits()
        today_h = hd.get(today_str_s, {})
        done = [k for k, v in today_h.items() if v]
        not_done = [k for k, v in today_h.items() if not v]
        if today_h:
            habits_facts = f"Виконано {len(done)}/{len(today_h)}"
            if done: habits_facts += f": {', '.join(done[:4])}"
            if not_done: habits_facts += f". Залишилось: {', '.join(not_done[:3])}"
    except: pass

    # Календар
    cal_facts = ""
    if calendar_text and "нічого не заплановано" not in calendar_text.lower():
        ev_m = _re.findall(r"(\d{2}:\d{2})[^\n]*<b>(.{2,50}?)</b>", calendar_text)
        if ev_m:
            cal_facts = "; ".join(f"{t} {n}" for t, n in ev_m[:5])
        else:
            ev_m2 = _re.findall(r"—\s*<b>(.{2,50}?)</b>", calendar_text)
            if ev_m2:
                cal_facts = "; ".join(ev_m2[:5])

    # Email
    email_facts = ""
    email_drafts_context = ""
    if email_text:
        # Новий формат: dict з __email_block__
        if isinstance(email_text, dict) and email_text.get("__email_block__"):
            items = email_text.get("items", [])
            unread_cnt = len(items)
            skip_kw = ["newsletter", "noreply", "no-reply", "notification",
                       "duolingo", "youtube", "medium", "unsubscribe"]
            important_emails = []
            for em in items[:5]:
                sender = em.get("sender", "")
                subj = em.get("subject", "")
                if not any(k in sender.lower() for k in skip_kw):
                    important_emails.append({"subject": subj, "sender": sender, "summary": ""})
            if important_emails:
                email_facts = f"Непрочитаних: {unread_cnt}. Важливі: " + "; ".join(f'"{e["subject"]}" від {e["sender"]}' for e in important_emails[:3])
                email_drafts_context = "\n".join(f'- Лист від "{e["sender"]}": {e["subject"]}' for e in important_emails[:3])
            elif unread_cnt > 0:
                email_facts = f"Нових листів: {unread_cnt}"
        else:
            # Старий формат: рядок (fallback)
            _et = str(email_text)
            unread_m = _re.search(r"🔴\s*(\d+)\s*нових", _et)
            unread_cnt = int(unread_m.group(1)) if unread_m else 0
            subjects = _re.findall(r"📨\s*<b>(.{3,60}?)</b>", _et)
            senders = _re.findall(r"👤\s*<code>(.{3,50}?)</code>", _et)
            ai_sums = _re.findall(r"🤖\s*(.{5,200}?)(?:\n|$)", _et)
            skip_kw = ["newsletter", "noreply", "no-reply", "notification",
                       "duolingo", "youtube", "medium", "unsubscribe"]
            important_emails = []
            for i, subj in enumerate(subjects[:5]):
                sender = senders[i] if i < len(senders) else ""
                ai_s = ai_sums[i] if i < len(ai_sums) else ""
                if not any(k in sender.lower() for k in skip_kw):
                    important_emails.append({"subject": subj, "sender": sender, "summary": ai_s})
            if important_emails:
                email_facts = f"Непрочитаних: {unread_cnt}. Важливі: " + "; ".join(f'"{e["subject"]}" від {e["sender"]}' for e in important_emails[:3])
                email_drafts_context = "\n".join(f'- Лист від "{e["sender"]}": {e["subject"]}. Суть: {e["summary"]}' for e in important_emails[:3])
            elif unread_cnt > 0:
                email_facts = f"Нових листів: {unread_cnt}"

    # Астро
    astro_facts = ""
    if astro_text:
        tense = _re.findall(r"🔴[^\n<]+", astro_text)
        good = _re.findall(r"🟢[^\n<]+", astro_text)
        parts_a = []
        if tense: parts_a.append(_re.sub(r"<[^>]+>","",tense[0]).strip())
        if good: parts_a.append(_re.sub(r"<[^>]+>","",good[0]).strip())
        if parts_a: astro_facts = "; ".join(parts_a)

    # Зміна — точний опис з реальним статусом "зараз"
    is_working_now = shift_ctx.get("is_working_now", False)
    if shift == "early":
        if 6 <= h < 18:
            shift_desc = "рання зміна 06:00–18:00 (ЗАРАЗ НА РОБОТІ)"
        elif h >= 18:
            shift_desc = "рання зміна вже закінчилась (06:00–18:00), вдома"
        else:
            shift_desc = "рання зміна сьогодні (06:00–18:00), ще не почалась"
    elif shift == "night":
        if h >= 17 or h < 6:
            shift_desc = "нічна зміна 17:00–05:00 (ЗАРАЗ НА РОБОТІ)"
        else:
            shift_desc = "нічна зміна сьогодні ввечері (17:00–05:00), вдома"
    elif shift == "after_night":
        shift_desc = "вдома після нічної зміни (зміна закінчилась, відпочиває)"
    else:
        shift_desc = "вільний день, вдома"

    # ── Завантажуємо попередні підсумки (останні 5) ───────────────────────────
    prev_summaries_ctx = ""
    try:
        prev_data = storage.load("summaries_history.json", default=[])
        if prev_data:
            recent = prev_data[-5:]
            prev_summaries_ctx = "\n".join(
                f"[{p['date']} {p.get('time','')}] {p['text'][:300]}"
                for p in recent
            )
    except: pass

    # ── QWatch Pro дані ───────────────────────────────────────────────────────
    qwatch_facts = ""
    try:
        qw_all = storage.load("qwatch_data.json", default={})
        if qw_all:
            qw_today = qw_all.get(today_str_s)
            if not qw_today:
                # беремо останній доступний запис (не старіше 2 днів)
                from datetime import timedelta
                yesterday = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
                qw_today = qw_all.get(yesterday)
            if qw_today:
                parts_qw = []
                if qw_today.get("health_score"):
                    parts_qw.append(f"Health Score: {qw_today['health_score']}/100")
                if qw_today.get("sleep_total_min"):
                    sh, sm = divmod(int(qw_today["sleep_total_min"]), 60)
                    parts_qw.append(f"Сон: {sh}г {sm:02d}хв")
                    if qw_today.get("sleep_deep_min"):
                        parts_qw.append(f"глибокий: {qw_today['sleep_deep_min']}хв")
                    if qw_today.get("sleep_quality"):
                        parts_qw.append(f"якість сну: {qw_today['sleep_quality']}")
                if qw_today.get("hr_avg"):
                    parts_qw.append(f"ЧСС: {qw_today['hr_avg']} уд/хв")
                if qw_today.get("hrv"):
                    parts_qw.append(f"HRV: {qw_today['hrv']} мс")
                if qw_today.get("stress"):
                    parts_qw.append(f"стрес: {qw_today['stress']}")
                if qw_today.get("spo2"):
                    parts_qw.append(f"SpO2: {qw_today['spo2']}%")
                if qw_today.get("steps"):
                    parts_qw.append(f"кроки: {int(qw_today['steps']):,}")
                if parts_qw:
                    qwatch_facts = ", ".join(parts_qw)
    except Exception as _qe:
        print(f"get_summary qwatch error: {_qe}")

    # ── Портфель ──────────────────────────────────────────────────────────────
    portfolio_facts = ""
    try:
        from portfolio import get_portfolio_summary as _get_pf
        _pf = _get_pf()
        if _pf:
            _tv = _pf.get("total_value", 0)
            _pnl = _pf.get("total_pnl")
            _ch24 = _pf.get("change_24h", _pf.get("total_change24", 0))
            _pf_parts = [f"Загальна вартість: ${_tv:,.0f}"]
            if _pnl is not None:
                _pf_parts.append(f"P&L: ${_pnl:+,.0f}")
            _pf_parts.append(f"Зміна 24г: ${_ch24:+,.0f}")
            _pos_raw = _pf.get("positions", {})
            # positions може бути dict {sym: {...}} або list
            if isinstance(_pos_raw, dict):
                _pos_list = [{"symbol": sym, **data} for sym, data in _pos_raw.items()]
            else:
                _pos_list = list(_pos_raw)
            if _pos_list:
                _top = sorted(_pos_list, key=lambda x: x.get("value", 0), reverse=True)[:5]
                _pos_str = ", ".join(
                    f"{p.get('symbol','?')} ${p.get('value',0):,.0f}"
                    + (f" P&L:{p['pnl_pct']:+.1f}%" if p.get('pnl_pct') is not None else "")
                    for p in _top
                )
                _pf_parts.append(f"Топ позиції: {_pos_str}")
            portfolio_facts = ". ".join(_pf_parts)
    except Exception as _pfe:
        print(f"get_summary portfolio error: {_pfe}")

    # ── Біг (Strava) ──────────────────────────────────────────────────────────
    run_facts = ""
    try:
        from strava import get_month_stats as _gms_s, get_last_activity as _gla_s
        _now_s = datetime.now(timezone.utc) + timedelta(hours=2)
        _ms = _gms_s(_now_s.year, _now_s.month)
        _runs_cnt = _ms.get("runs", 0)
        _runs_km = _ms.get("km", _ms.get("distance_km", 0))
        _run_parts = [f"Цей місяць: {_runs_cnt} пробіжок, {_runs_km:.1f} км"]
        # Остання пробіжка
        try:
            _lr = _gla_s()
            if _lr:
                _run_parts.append(
                    f"Остання: {_lr.get('distance_km',0):.1f} км "
                    f"темп {_lr.get('pace','?')} "
                    f"({_lr.get('when','?')}, {_lr.get('date','')})"
                )
        except Exception: pass
        run_facts = ". ".join(_run_parts)
    except Exception as _rfe:
        print(f"get_summary run error: {_rfe}")

    # ── Будуємо промпт для Gemini ─────────────────────────────────────────────
    sections_data = {
        "🌤 ПОГОДА": weather_facts,
        "💹 КРИПТО": crypto_facts,
        "💼 ПОРТФЕЛЬ": portfolio_facts or "—",
        "⚖️ ВАГА": weight_facts or "—",
        "🏃 БІГ": run_facts or "—",
        "🏃 АКТИВНІСТЬ (кроки)": steps_facts or "—",
        "💊 ЛІКИ": meds_facts or "—",
        "✅ ЗВИЧКИ": habits_facts or "—",
        "📅 КАЛЕНДАР": cal_facts or "Нічого",
        "📧 ПОШТА": email_facts or "—",
        "🔮 АСТРО": astro_facts or "—",
        "⌚ QWATCH": qwatch_facts or "—",
    }

    sections_str = "\n".join(f"{k}: {v}" for k, v in sections_data.items() if v and v != "—")

    prev_ctx_block = ""
    if prev_summaries_ctx:
        prev_ctx_block = f"""
Попередні підсумки (для розуміння трендів):
{prev_summaries_ctx}
"""

    drafts_block = ""
    if email_drafts_context:
        drafts_block = f"""
Для кожного важливого листа — коротка чернетка відповіді (1-3 речення):
Формат: ✍️ Чернетка для [відправник]: [текст]
Листи:
{email_drafts_context}"""

    prompt = f"""Ти персональний AI-асистент Олега Новосадова (Кошіце, Словаччина).
Час: {now_local.strftime('%H:%M')}, {today_str_s}.
Статус: {shift_desc}.
ВАЖЛИВО: якщо в статусі написано "НА РОБОТІ" — Олег зараз фізично на роботі. Якщо "вдома" — вдома. Ніколи не плутай це і не суперечь статусу.

КРИТИЧНО ВАЖЛИВО — ЗАЛІЗНІ ПРАВИЛА:
1. Пиши ТІЛЬКИ те що є в даних нижче. Жодних припущень ("мабуть", "можливо", "сподіваюся", "судячи з", "напевно", "схоже").
2. Якщо якогось числа/факту немає в даних — НЕ ЗГАДУЙ цей блок взагалі. Мовчання краще ніж вигадка.
3. Кожне речення має опиратись на конкретне число або факт з розділу ДАНІ нижче.
4. Не переформульовуй дані — аналізуй їх: що означає це число, що треба робити, куди тренд іде.

═══ ДАНІ (тільки це є реальним) ═══
{sections_str}
{prev_ctx_block}
════════════════════════════════════

Напиши аналіз рівно 100 речень. Тон: прямий, як хороший тренер і фінансовий партнер — без води.

БЛОКИ (пиши тільки якщо є реальні дані, інакше пропускай):

🌤 <b>ПОГОДА</b>
Конкретна температура з даних. Вологість/вітер якщо є. Чи підходить сьогодні для бігу на вулиці — однозначно так або ні з поясненням чому.

💹 <b>КРИПТО ТА ПОРТФЕЛЬ</b>
BTC/ETH/AVAX/ONDO — точна ціна і % за 24г з даних. Загальна вартість портфеля і P&L якщо є. Порівняй з попередніми підсумками якщо є — ріст чи падіння. Чітка рекомендація: тримати, докупити, або зафіксувати — з обґрунтуванням на основі цифр.

⚖️ <b>ЗДОРОВ'Я</b>
Точна вага з даних і скільки кг до цілі 78. Тренд ваги якщо є попередні підсумки. Сон/HRV/ЧСС/кроки тільки якщо є в даних QWatch. Біг: точна кількість км цього місяця з даних і скільки залишилось до 40 км плану.

✅ <b>ЗВИЧКИ ТА ДЕНЬ</b>
Які звички позначені як виконані, які ні — тільки з реальних даних. Конкретні події з календаря якщо є. Що зробити в найближчі 2 години виходячи з часу і зміни.

📧 <b>ПОШТА</b>
Тільки якщо є реальні листи в даних: від кого, тема, що треба зробити.
{drafts_block}

🔮 <b>АСТРО</b>
Тільки якщо є астро-дані: конкретні впливи з даних і як вони стосуються рішень сьогодні — по крипто, здоров'ю, стосункам.

🎯 <b>ВИСНОВОК</b>
Одна конкретна дія прямо зараз. Що покращилось vs вчора (тільки якщо є порівняльні дані). Що погіршилось. Один крок на наступну годину.

Мова: українська. Тільки <b> для заголовків. Seed: {slot_seed}"""

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    ai_summary = ""
    if gemini_key:
        try:
            body_ai = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.7},
            }).encode()
            req_ai = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                data=body_ai, headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req_ai, timeout=45) as r_ai:
                resp_ai = json.loads(r_ai.read())
            ai_summary = resp_ai["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            print(f"get_summary gemini error: {e}")

    # ── Зберігаємо підсумок в history ────────────────────────────────────────
    if ai_summary:
        try:
            prev_data = storage.load("summaries_history.json", default=[])
            if not isinstance(prev_data, list):
                prev_data = []
            prev_data.append({
                "date": today_str_s,
                "time": now_local.strftime("%H:%M"),
                "text": ai_summary
            })
            # Зберігаємо останні 60 підсумків (20 днів * 3 рази)
            prev_data = prev_data[-60:]
            storage.save("summaries_history.json", prev_data)
        except Exception as _se:
            print(f"[summary history] save error: {_se}")

    if not ai_summary:
        # Fallback без Gemini
        lines_fb = []
        if shift == "after_night":
            lines_fb.append("🛋 <b>Після нічної.</b> Відновлення важливіше за активність.")
        elif 5 <= h < 10:
            lines_fb.append("🌅 Доброго ранку!")
        elif 10 <= h < 17:
            lines_fb.append("☀️ Добрий день!")
        else:
            lines_fb.append("🌙 Добрий вечір!")
        for k, v in sections_data.items():
            if v and v != "—":
                lines_fb.append(f"{k}: {v}")
        ai_summary = "\n".join(lines_fb)

    header = "🗂 <b>ПІДСУМОК</b>"
    return f"{header}\n\n{ai_summary}"





# ─── MAIN ─────────────────────────────────────────────────────────────────────

def get_city_traffic():
    """Ситуація на дорогах Košice через TomTom — інциденти."""
    try:
        import sys, os as _os
        sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from traffic_kosice import format_traffic_report
        return format_traffic_report()
    except Exception as e:
        print(f"Traffic error: {e}")
        return None


# ─── MAIN ─────────────────────────────────────────────────────────────────────

MAIN_SENT_FILE = os.path.join(_DATA_DIR, "monitor_main_sent.json")

_GH_DATA_BRANCH = "data"  # окрема гілка для даних — не тригерить Railway

def _gh_get_sent():
    """Читає monitor_main_sent.json з GitHub гілки data."""
    import base64
    gh_token = os.environ.get("GITHUB_TOKEN", "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")
    if not gh_token:
        return None, None
    url = f"https://api.github.com/repos/NovosadovO/morning-report/contents/data/monitor_main_sent.json?ref={_GH_DATA_BRANCH}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {gh_token}",
        "User-Agent": "morning-report-bot"
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
            content = json.loads(base64.b64decode(d["content"]).decode())
            return content, d["sha"]
    except Exception:
        return {}, None

def _gh_get_json(filename):
    """Читає довільний JSON-файл з GitHub гілки data. Повертає (dict, sha)."""
    import base64
    gh_token = os.environ.get("GITHUB_TOKEN", "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")
    if not gh_token:
        return {}, None
    url = f"https://api.github.com/repos/NovosadovO/morning-report/contents/data/{filename}?ref={_GH_DATA_BRANCH}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {gh_token}",
        "User-Agent": "morning-report-bot"
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
            content = json.loads(base64.b64decode(d["content"]).decode())
            return content, d["sha"]
    except Exception:
        return {}, None

def _gh_save_json(filename, data, sha):
    """Зберігає довільний JSON-файл на GitHub гілку data."""
    import base64
    gh_token = os.environ.get("GITHUB_TOKEN", "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")
    if not gh_token:
        return
    url = f"https://api.github.com/repos/NovosadovO/morning-report/contents/data/{filename}"
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
    body_dict = {
        "message": f"dedup: update {filename}",
        "content": content,
        "branch": _GH_DATA_BRANCH,
    }
    if sha:
        body_dict["sha"] = sha
    body = json.dumps(body_dict).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"token {gh_token}",
        "Content-Type": "application/json",
        "User-Agent": "morning-report-bot"
    }, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            pass
    except Exception as e:
        print(f"_gh_save_json({filename}) error: {e}")

def _gh_save_sent(data, sha):
    """Зберігає monitor_main_sent.json на GitHub гілку data."""
    import base64
    gh_token = os.environ.get("GITHUB_TOKEN", "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")
    if not gh_token:
        return
    url = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/monitor_main_sent.json"
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
    body_dict = {
        "message": "dedup: mark slot sent",
        "content": content,
        "branch": _GH_DATA_BRANCH,
    }
    if sha:
        body_dict["sha"] = sha
    body = json.dumps(body_dict).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"token {gh_token}",
        "Content-Type": "application/json",
        "User-Agent": "morning-report-bot"
    }, method="PUT")
    try:
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        print(f"_gh_save_sent error: {e}")

def _get_report_slot(now_local):
    """
    1 слот на годину: тільки :00
    Повертає ключ слоту або None якщо ми не у вікні.
    Вікно: 0-4хв кожної години
    """
    m = now_local.minute
    h = now_local.hour
    date_str = now_local.strftime("%Y-%m-%d")
    if 0 <= m < 5:
        return f"{date_str}T{h:02d}:00"
    return None


def _build_report_header(now_local, slot_key, cal_events_raw):
    """
    Єдиний чистий стиль заголовку з контекстом дня:
    - Іконка часу доби + час + дата/день
    - Рядок локації/типу дня (вдома / на роботі / вихідний)
    - Мотиваційна фраза відповідно до реального часу
    - Подія з календаря (якщо є)
    """
    import hashlib as _hsh
    h = now_local.hour
    weekday_ua   = ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"][now_local.weekday()]
    weekday_full = ["понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя"][now_local.weekday()]
    time_str = now_local.strftime("%H:%M")
    date_str = now_local.strftime("%d.%m")
    is_weekend = now_local.weekday() >= 5

    # Seed для вибору фрази (стабільний для слоту, різний для кожного часу)
    seed_int = int(_hsh.md5(slot_key.encode()).hexdigest(), 16)

    # ── Час доби — правильно розбитий ──────────────────────────────────────
    if 4 <= h < 7:
        period = "early_morning"   # 04–07: рання зміна / дуже ранній підйом
    elif 7 <= h < 11:
        period = "morning"         # 07–11: звичайний ранок
    elif 11 <= h < 14:
        period = "midday"          # 11–14: обід
    elif 14 <= h < 18:
        period = "afternoon"       # 14–18: після обіду
    elif 18 <= h < 22:
        period = "evening"         # 18–22: вечір
    else:
        period = "night"           # 22–04: ніч

    # ── Іконка та фрази залежно від часу ───────────────────────────────────
    _icons = {
        "early_morning": "🌄",
        "morning":       "🌅",
        "midday":        "☀️",
        "afternoon":     "🌆",
        "evening":       "🌙",
        "night":         "🌃",
    }
    _period_icon = _icons[period]

    _vibes = {
        "early_morning": [
            "Ранній підйом — ти вже попереду 💪",
            "04:хх — рання зміна, вперед! ⚡",
            "Рано встав — день виграв 🌄",
            "Підйом! Ранкова зміна чекає 🏭",
        ],
        "morning": [
            "Ранок вирішує день! 🌅",
            "Доброго ранку, Олег! ☕ Заряджаємось.",
            "Новий день — нові можливості 💪",
            "Ранок — найпродуктивніший час! 🚀",
        ],
        "midday": [
            "Половина дня позаду — тримаємо темп 🔥",
            "Середина дня — перевіряємо пульс 📡",
            "Не забудь нормально поїсти 😄",
            "11–14: найкращий час для складних рішень 🧠",
        ],
        "afternoon": [
            "Після обіду — фокус! 🎯",
            "Друга половина дня, Олег 💼",
            "Час для справ 📋",
            "Фінальний відрізок дня 🏁",
        ],
        "evening": [
            "Вечір — підбиваємо підсумки 🌙",
            "Гарний день? Занотуй результати ✍️",
            "Вечірній огляд — всі показники ✅",
            "Завтра буде ще кращий день! 🌟",
        ],
        "night": [
            "Вже пізно — не забудь відпочити 😴",
            "Нічний моніторинг 🦉",
            "Тихо навколо — час для себе 🌌",
            "Опівніч — зберігай сили 💤",
        ],
    }
    _vibe = _vibes[period][seed_int % len(_vibes[period])]

    # ── Контекст дня: де знаходиться і який тип дня ────────────────────────
    try:
        _sc = _get_current_shift_context(cal_events_raw or "")
        _shift      = _sc.get("shift", "free")
        _working    = _sc.get("is_working_now", False)
    except Exception:
        _shift, _working = "free", False

    if is_weekend:
        _day_ctx = "🏖 Вихідний"
    elif _working:
        if _shift == "early":
            _day_ctx = "🏭 На роботі  ·  Рання зміна"
        elif _shift == "night":
            _day_ctx = "🏭 На роботі  ·  Нічна зміна"
        else:
            _day_ctx = "🏭 На роботі"
    elif _shift == "early" and h < 6:
        _day_ctx = "🏠 Вдома  ·  Готується до ранньої"
    elif _shift == "night" and h < 18:
        _day_ctx = "🏠 Вдома  ·  Нічна зміна сьогодні"
    else:
        _day_ctx = "🏠 Вдома"

    # ── Підказка з календаря ───────────────────────────────────────────────
    cal_hint = ""
    if cal_events_raw and "нічого не заплановано" not in cal_events_raw.lower():
        import re as _re
        ev_names = _re.findall(r"—\s*<b>(.{2,40}?)</b>", cal_events_raw)
        if ev_names:
            cal_hint = (
                f"\n📌 {esc(ev_names[0])}"
                if len(ev_names) == 1
                else f"\n📌 {esc(ev_names[0])} +{len(ev_names)-1}"
            )

    # ── Єдиний стиль заголовку ─────────────────────────────────────────────
    header = (
        f"{_period_icon}\n"
        f"<b>ЗВІТ  ·  {weekday_ua} {date_str}  ·  {time_str}</b>\n"
        f"{_day_ctx}\n"
        f"<i>{_vibe}</i>"
        f"{cal_hint}"
    )
    return header


def _get_calendar_context_for_report():
    """Витягує події з УСІХ календарів (включно з нагадуваннями, завданнями, ДН)."""
    token = _calendar_access_token()
    if not token:
        return [], "нічого не заплановано"
    try:
        headers = {"Authorization": f"Bearer {token}"}
        now = datetime.now(timezone.utc)
        now_local = now + timedelta(hours=2)
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        today_end = today_start + timedelta(hours=48)
        # Читаємо ВСІ календарі
        events = _fetch_events_all_calendars(headers, today_start, today_end, max_per_cal=20)
        result = []
        for ev in events:
            start = ev["start"].get("dateTime") or ev["start"].get("date")
            summary = ev.get("summary", "")
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                tz_local = timezone(timedelta(hours=2))
                t = dt.astimezone(tz_local).strftime("%H:%M") if "T" in start else "весь день"
                ev_date = dt.astimezone(tz_local).strftime("%Y-%m-%d")
            except:
                t = start; ev_date = ""
            result.append({"summary": summary, "time": t, "date": ev_date, "raw_start": start})
        text_parts = [f"{e['time']} {e['summary']}" for e in result if e['date'] == now_local.strftime("%Y-%m-%d")]
        return result, (", ".join(text_parts) if text_parts else "нічого не заплановано")
    except Exception as e:
        print(f"_get_calendar_context_for_report error: {e}")
        return [], "нічого не заплановано"


def _format_weather_visual(weather_text):
    """Форматує погоду з мінімалістичним візуальним стилем."""
    import re as _re
    if not weather_text:
        return None
    # Витягуємо ключові дані
    temp_m = _re.search(r"([-−]?\d+)[°℃]", weather_text)
    feels_m = _re.search(r"(?:відчув|feels)[^\d]*([-−]?\d+)", weather_text, _re.I)
    humid_m = _re.search(r"вологість[:\s]*([\d]+)%", weather_text, _re.I)
    wind_m = _re.search(r"вітер[:\s]*([\d.]+)", weather_text, _re.I)
    desc_m = _re.search(r"(?:Опис|desc|:)\s*([а-яА-ЯіїєёІЇЄ ,а-я]+?)(?:\n|$)", weather_text)

    if not temp_m:
        return weather_text  # fallback

    temp = int(temp_m.group(1).replace("−", "-"))
    feels = int(feels_m.group(1).replace("−", "-")) if feels_m else temp

    # Погодний емоджі
    wl = weather_text.lower()
    if "гроза" in wl: w_icon = "⛈"
    elif "злива" in wl or "сильний дощ" in wl: w_icon = "🌧"
    elif "дощ" in wl: w_icon = "🌦"
    elif "хмарно" in wl and "хмарно без опадів" not in wl: w_icon = "☁️"
    elif "ясно" in wl or "сонячно" in wl: w_icon = "☀️"
    elif "туман" in wl: w_icon = "🌫"
    elif "сніг" in wl: w_icon = "❄️"
    elif "мряка" in wl: w_icon = "🌧"
    else: w_icon = "🌤"

    # Температурний колір
    if temp < 0: t_style = "❄️"
    elif temp < 10: t_style = "🥶"
    elif temp < 20: t_style = "😊"
    elif temp < 28: t_style = "☀️"
    else: t_style = "🥵"

    # Поради
    advice = []
    if "дощ" in wl or "злива" in wl: advice.append("☂️ парасолька")
    if "гроза" in wl: advice.append("🏠 краще вдома")
    if temp < 0: advice.append("🧣 мороз!")
    elif temp < 8: advice.append("🧥 куртка")
    elif temp > 28: advice.append("💧 пий воду")
    if "туман" in wl: advice.append("🚗 обережно на дорозі")

    result = f"🌡 <b>ПОГОДА ЗАРАЗ</b>\n"
    result += f"{w_icon} <b>{temp}°C</b>"
    if feels != temp:
        result += f"  (відчув. {feels}°)"
    if humid_m: result += f"  💧{humid_m.group(1)}%"
    if wind_m: result += f"  🌬{wind_m.group(1)} м/с"
    if advice:
        result += f"\n<i>{'  ·  '.join(advice)}</i>"
    return result


def _format_prices_visual(prices_text, cal_events_text=""):
    """Форматує крипто з акцентом на зміні + calendar-aware порада."""
    import re as _re
    if not prices_text:
        return None

    up = prices_text.count("🔺")
    dn = prices_text.count("🔻")

    if up > dn + 1:
        market = "🟢 БИЧАЧИЙ"
        market_tip = "Ринок зелений — гарний час переглянути портфель."
    elif dn > up + 1:
        market = "🔴 ВЕДМЕЖИЙ"
        market_tip = "Ринок падає — не панікуй, стеж за стоп-лосами."
    else:
        market = "🟡 НЕЙТРАЛЬНИЙ"
        market_tip = "Бокова торгівля — жодних різких рухів."

    # Якщо є вільний час — додаємо контекстну пораду
    if "вихідний" in cal_events_text.lower() or not cal_events_text or "нічого" in cal_events_text:
        tip_line = f"\n<i>💡 {market_tip}</i>"
    else:
        tip_line = ""

    # Витягуємо монети
    coins = []
    for coin in ["BTC", "ETH", "AVAX", "ONDO"]:
        row_m = _re.search(r"[^\n]*" + coin + r"[^\n]*", prices_text)
        if not row_m: continue
        row = row_m.group(0)
        price_m = _re.search(r"\$([\d,]+(?:\.\d+)?)", row)
        pct_m = _re.search(r"([+\-−\+][\d.]+)%", row)
        pct3h_m = _re.search(r"\[pct3h:([+\-][\d.]+)\]", row)
        if not price_m: continue
        price = price_m.group(1)
        pct_val = float(pct_m.group(1).replace("−", "-")) if pct_m else 0
        trend_icon = "🔺" if pct_val > 0 else ("🔻" if pct_val < 0 else "➡️")
        pct_str = (("+" if pct_val > 0 else "") + f"{pct_val:.2f}%") if pct_m else ""
        # % від попереднього звіту
        if pct3h_m:
            p3h = float(pct3h_m.group(1))
            sign3h = "+" if p3h >= 0 else ""
            prev_str = f"  <i>({sign3h}{p3h:.2f}% від попер.)</i>"
        else:
            prev_str = ""
        coins.append(f"{trend_icon} <b>{coin}</b> <code>${price}</code>  {pct_str}{prev_str}")

    header = f"💰 <b>КРИПТО</b>  ·  {market}"
    body = "\n".join(coins) if coins else prices_text[:300]
    return f"{header}\n{body}{tip_line}"


def generate_crypto_trend_chart(days: int = 30) -> bytes | None:
    """
    Генерує PNG з лінійними графіками цін BTC/ETH/AVAX/ONDO за N днів.
    Темна тема, 2×2, великі шрифти, чіткі дати і числа.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import matplotlib.ticker as mticker
        import numpy as np
        from datetime import datetime as dt
        import io, time as _t

        COINS_MAP = [
            ("BTC", "bitcoin",      "#F7931A"),
            ("ETH", "ethereum",     "#627EEA"),
            ("AVAX","avalanche-2",  "#E84142"),
            ("ONDO","ondo-finance", "#00C6A2"),
        ]
        BG    = "#0D1117"
        PANEL = "#161B22"
        GRID  = "#1E2530"
        TEXT  = "#E6EDF3"
        MUTED = "#8B949E"
        BORDER= "#30363D"

        hist   = storage.load_price_history()
        cutoff = _t.time() - days * 86400

        fig, axes = plt.subplots(2, 2, figsize=(20, 13))
        fig.patch.set_facecolor(BG)
        fig.subplots_adjust(hspace=0.55, wspace=0.38, left=0.07, right=0.97, top=0.90, bottom=0.08)

        has_any_data = False

        for ax, (sym, cid, color) in zip(axes.flat, COINS_MAP):
            ax.set_facecolor(PANEL)
            for spine in ax.spines.values():
                spine.set_edgecolor(BORDER)
                spine.set_linewidth(1.0)
            ax.tick_params(colors=MUTED, labelsize=12, length=4)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
            ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))

            pts = [p for p in hist.get(cid, []) if p[0] >= cutoff]
            pts.sort(key=lambda x: x[0])

            if len(pts) < 2:
                ax.text(0.5, 0.5, "накопичується...", ha="center", va="center",
                        color=MUTED, transform=ax.transAxes, fontsize=13)
                ax.set_title(sym, color=color, fontsize=16, fontweight="bold", pad=10)
                continue

            has_any_data = True
            timestamps = [dt.utcfromtimestamp(p[0]) for p in pts]
            prices     = [p[1] for p in pts]
            p_min, p_max = min(prices), max(prices)

            # Лінія + заливка
            ax.plot(timestamps, prices, color=color, linewidth=2.8, zorder=3)
            ax.fill_between(timestamps, prices, p_min * 0.998,
                            color=color, alpha=0.18, zorder=2)

            # Тренд-лінія
            x_num  = np.array([(t - timestamps[0]).total_seconds() for t in timestamps])
            coeffs = np.polyfit(x_num, prices, 1)
            t_color = "#3FB950" if coeffs[0] >= 0 else "#F85149"
            ax.plot(timestamps, np.polyval(coeffs, x_num), color=t_color,
                    linewidth=2.0, linestyle="--", alpha=0.85, zorder=4)

            # Мітка першої і останньої ціни
            def _fmt(v):
                return f"${v:,.2f}" if v < 10 else f"${v:,.0f}"
            ax.annotate(_fmt(prices[0]),
                xy=(timestamps[0], prices[0]),
                xytext=(6, 8), textcoords="offset points",
                color=MUTED, fontsize=10, fontweight="bold", va="center")
            ax.annotate(_fmt(prices[-1]),
                xy=(timestamps[-1], prices[-1]),
                xytext=(-6, 8), textcoords="offset points",
                color=color, fontsize=13, fontweight="bold", va="center",
                ha="right")

            ax.grid(True, color=GRID, linewidth=0.8, zorder=1)
            ax.set_ylim(p_min * 0.993, p_max * 1.03)

            ch   = (prices[-1] - prices[0]) / prices[0] * 100
            sign = "+" if ch >= 0 else ""
            ch_color = "#3FB950" if ch >= 0 else "#F85149"

            ax.set_title(f"{sym}  {_fmt(prices[-1])}", color=TEXT, fontsize=16,
                         fontweight="bold", pad=8, loc="left")
            ax.text(0.99, 1.03, f"{sign}{ch:.1f}%", transform=ax.transAxes,
                    color=ch_color, fontsize=14, fontweight="bold",
                    ha="right", va="bottom")

            # Y-вісь форматування
            if prices[-1] >= 1000:
                ax.yaxis.set_major_formatter(mticker.FuncFormatter(
                    lambda v, _: f"${v/1000:.0f}k"))
            elif prices[-1] >= 10:
                ax.yaxis.set_major_formatter(mticker.FuncFormatter(
                    lambda v, _: f"${v:.0f}"))
            else:
                ax.yaxis.set_major_formatter(mticker.FuncFormatter(
                    lambda v, _: f"${v:.3f}"))
            ax.yaxis.set_tick_params(labelcolor=MUTED, labelsize=11)
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right", fontsize=11)

        if not has_any_data:
            print("[generate_crypto_trend_chart] no history data yet")
            plt.close(fig)
            return None

        from datetime import datetime as _dtnow
        _now_label = (_dtnow.utcnow()).strftime("%d.%m.%Y %H:%M UTC")
        fig.suptitle(f"BTC / ETH / AVAX / ONDO  ·  {days}d  ·  {_now_label}",
                     color=TEXT, fontsize=17, fontweight="bold", y=0.96)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                    facecolor=BG, edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        print(f"[generate_crypto_trend_chart] error: {e}")
        return None


def generate_weight_trend_chart(days: int = 30) -> bytes | None:
    """
    Генерує PNG з тренд-лінією ваги (останні N точок).
    Темна тема, великі шрифти, fill_between, ціль 78 кг пунктиром, тренд-лінія.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import numpy as np
        import io
        from datetime import datetime as _dt, timedelta as _td

        BG          = "#0D1117"
        PANEL       = "#161B22"
        GRID        = "#1E2530"
        BORDER      = "#30363D"
        TEXT        = "#E6EDF3"
        MUTED       = "#8B949E"
        GOAL_COLOR  = "#58A6FF"
        LINE_COLOR  = "#3FB950"

        try:
            import storage as _storage_chart
            # weight_data.json — актуальний файл (weight.py зберігає сюди)
            raw = _storage_chart.load("weight_data.json") or {}
            if not raw:
                # fallback на старий weight.json
                raw = _storage_chart.load_weight() or {}
        except Exception:
            return None

        if not raw:
            return None

        entries = []
        for date_str, w in raw.items():
            try:
                d = _dt.strptime(date_str, "%Y-%m-%d").date()
                if w is not None:
                    entries.append((d, float(w)))
            except Exception:
                continue
        entries.sort(key=lambda x: x[0])

        # Беремо всі записи за останні N днів (за датою, не за кількістю точок)
        cutoff = (_dt.utcnow() - _td(days=days)).date()
        entries = [(d, w) for d, w in entries if d >= cutoff]

        if len(entries) < 2:
            return None

        dates   = [e[0] for e in entries]
        weights = [e[1] for e in entries]
        x_dates = [_dt.combine(d, _dt.min.time()) for d in dates]

        fig, ax = plt.subplots(figsize=(18, 7), facecolor=BG)
        ax.set_facecolor(PANEL)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER)
            spine.set_linewidth(1.2)

        # Fill between
        ax.fill_between(x_dates, weights, min(weights) - 0.5,
                        alpha=0.20, color=LINE_COLOR)

        # Лінія ваги
        ax.plot(x_dates, weights, color=LINE_COLOR, linewidth=3.0,
                marker="o", markersize=7, markerfacecolor=LINE_COLOR,
                zorder=3, label="Вага")

        # Тренд-лінія
        if len(weights) >= 4:
            xn = np.arange(len(weights))
            z  = np.polyfit(xn, weights, 1)
            p  = np.poly1d(z)
            trend_col = "#F85149" if z[0] > 0 else "#3FB950"
            ax.plot(x_dates, p(xn), "--", color=trend_col,
                    linewidth=2.2, alpha=0.85, label="Тренд")

        # Ціль 78 кг
        ax.axhline(78.0, color=GOAL_COLOR, linewidth=2.0,
                   linestyle=":", alpha=0.85, label="Ціль 78 кг")

        # Мітки кожної точки
        for xi, (xd, w) in enumerate(zip(x_dates, weights)):
            ax.annotate(f"{w:.1f}",
                        (xd, w),
                        textcoords="offset points", xytext=(0, 10),
                        color=TEXT, fontsize=9, ha="center")

        # Мітки першої і останньої — великі
        ax.annotate(f"{weights[0]:.1f}",
                    (x_dates[0], weights[0]),
                    textcoords="offset points", xytext=(8, -14),
                    color=MUTED, fontsize=13, fontweight="bold")
        ax.annotate(f"{weights[-1]:.1f}",
                    (x_dates[-1], weights[-1]),
                    textcoords="offset points", xytext=(-8, -14),
                    color=LINE_COLOR, fontsize=15, fontweight="bold", ha="right")

        # Осі
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%Y"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=35,
                 ha="right", color=TEXT, fontsize=12)
        ax.yaxis.set_tick_params(labelcolor=TEXT, labelsize=12)
        ax.tick_params(colors=TEXT)
        ax.grid(True, color=GRID, linewidth=0.8, alpha=0.8)
        ax.set_ylabel("кг", color=TEXT, fontsize=13)

        # Заголовок
        delta    = round(weights[-1] - weights[0], 1)
        sign     = "+" if delta > 0 else ""
        to_goal  = round(weights[-1] - 78.0, 1)
        goal_txt = f"до 78 кг: -{to_goal} кг" if to_goal > 0 else "ціль досягнута! 🏆"
        from datetime import datetime as _dtnow2
        _now_label = _dtnow2.utcnow().strftime("%d.%m.%Y %H:%M")
        ax.set_title(
            f"Вага: {dates[0].strftime('%d.%m')}–{dates[-1].strftime('%d.%m.%Y')}  ({len(entries)} записів, {sign}{delta} кг)  {goal_txt}  ·  {_now_label}",
            color=TEXT, fontsize=15, fontweight="bold", pad=12)

        leg = ax.legend(fontsize=12, facecolor=PANEL, edgecolor=BORDER,
                        labelcolor=TEXT, framealpha=0.9)

        fig.tight_layout(pad=1.5)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, facecolor=BG, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        print(f"[generate_weight_trend_chart] error: {e}")
        return None


def generate_habits_chart(days: int = 30) -> bytes | None:
    """
    Генерує PNG з графіками звичок за останні N днів.
    Теплова карта для булевих звичок + лінійний для сну.
    Темна тема, великі шрифти, чіткі дати.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import numpy as np
        import io
        from datetime import datetime as _dt, timedelta as _td, date as _date

        BG     = "#0D1117"
        PANEL  = "#161B22"
        GRID   = "#1E2530"
        BORDER = "#30363D"
        TEXT   = "#E6EDF3"
        MUTED  = "#8B949E"

        BOOL_HABITS = [
            ("shower", "🚿 Душ",   "#58A6FF"),
            ("run",    "🏃 Пробіжка", "#3FB950"),
            ("water",  "💧 Вода",   "#1F6FEB"),
            ("tea",    "🍵 Чай",    "#D29922"),
            ("sauna",  "🧖 Сауна",  "#F85149"),
        ]
        SLEEP_COLOR = "#A371F7"

        try:
            from storage import load_habits as _lh
            raw = _lh() or {}
        except Exception:
            return None

        if not raw:
            return None

        # Будуємо масив дат (останні N днів)
        today = _date.today()
        all_dates = [today - _td(days=i) for i in range(days - 1, -1, -1)]

        # ── Збираємо дані ───────────────────────────────────────────────────
        bool_matrix = {}  # habit_key -> [0/1/None per day]
        sleep_vals  = []  # float or None per day

        for hkey, _, _ in BOOL_HABITS:
            bool_matrix[hkey] = []

        for d in all_dates:
            ds = d.isoformat()
            entry = raw.get(ds, None)
            for hkey, _, _ in BOOL_HABITS:
                if entry is None:
                    bool_matrix[hkey].append(np.nan)
                else:
                    v = entry.get(hkey)
                    if v is None:
                        bool_matrix[hkey].append(np.nan)
                    else:
                        bool_matrix[hkey].append(1.0 if v else 0.0)
            # Sleep
            if entry is None:
                sleep_vals.append(None)
            else:
                sv = entry.get("sleep")
                sleep_vals.append(float(sv) if sv is not None else None)

        # Перевіряємо чи є взагалі дані
        has_data = any(
            not np.isnan(v)
            for vals in bool_matrix.values()
            for v in vals
        ) or any(v is not None for v in sleep_vals)

        if not has_data:
            return None

        # ── Малюємо ─────────────────────────────────────────────────────────
        n_bool = len(BOOL_HABITS)
        has_sleep = any(v is not None for v in sleep_vals)
        n_rows = n_bool + (1 if has_sleep else 0)

        fig_h = 2.5 * n_rows + 1.5
        fig, axes = plt.subplots(n_rows, 1, figsize=(20, fig_h), facecolor=BG)
        if n_rows == 1:
            axes = [axes]
        fig.subplots_adjust(hspace=0.6, left=0.10, right=0.97, top=0.90, bottom=0.10)

        x_pos = list(range(len(all_dates)))
        date_labels = [d.strftime("%d.%m") for d in all_dates]

        # ── Булеві звички (bar chart: зелений=✅, червоний=❌, сірий=нема даних)
        for ax_i, (hkey, hlabel, hcolor) in enumerate(BOOL_HABITS):
            ax = axes[ax_i]
            ax.set_facecolor(PANEL)
            for spine in ax.spines.values():
                spine.set_edgecolor(BORDER)
                spine.set_linewidth(0.8)

            vals = bool_matrix[hkey]
            colors_bar = []
            for v in vals:
                if np.isnan(v):
                    colors_bar.append("#2D333B")
                elif v == 1.0:
                    colors_bar.append(hcolor)
                else:
                    colors_bar.append("#F85149")

            bars = ax.bar(x_pos, [1 if not np.isnan(v) else 0.3 for v in vals],
                          color=colors_bar, width=0.85, zorder=3)

            # Рахунок виконання
            done  = sum(1 for v in vals if not np.isnan(v) and v == 1.0)
            total = sum(1 for v in vals if not np.isnan(v))
            pct   = f"{done}/{total}" if total > 0 else "0/0"

            ax.set_yticks([])
            ax.set_xlim(-0.5, len(x_pos) - 0.5)
            ax.set_ylim(0, 1.4)
            ax.grid(False)

            # Підписи дат кожні 3 дні
            tick_pos  = [i for i in x_pos if i % 3 == 0]
            tick_labs = [date_labels[i] for i in tick_pos]
            ax.set_xticks(tick_pos)
            ax.set_xticklabels(tick_labs, color=TEXT, fontsize=11, rotation=30, ha="right")

            ax.set_ylabel(hlabel, color=hcolor, fontsize=13, fontweight="bold",
                          rotation=0, labelpad=5, ha="right", va="center")
            ax.yaxis.set_label_coords(-0.01, 0.5)

            # Підказка зверху
            ax.text(0.99, 1.18, f"✅ {pct}", transform=ax.transAxes,
                    color=hcolor, fontsize=12, fontweight="bold",
                    ha="right", va="top")

        # ── Сон (лінійний графік)
        if has_sleep:
            ax = axes[n_bool]
            ax.set_facecolor(PANEL)
            for spine in ax.spines.values():
                spine.set_edgecolor(BORDER)
                spine.set_linewidth(0.8)

            sx = [i for i, v in enumerate(sleep_vals) if v is not None]
            sy = [v for v in sleep_vals if v is not None]

            if len(sx) >= 2:
                ax.plot(sx, sy, color=SLEEP_COLOR, linewidth=2.5,
                        marker="o", markersize=7, zorder=3)
                ax.fill_between(sx, sy, 0, color=SLEEP_COLOR, alpha=0.15, zorder=2)

                # Лінія норми 8г
                ax.axhline(8.0, color="#58A6FF", linewidth=1.5,
                           linestyle=":", alpha=0.7, label="Норма 8г")

                # Мітки значень
                for xi, yi in zip(sx, sy):
                    ax.annotate(f"{yi:.0f}г",
                                (xi, yi), xytext=(0, 8),
                                textcoords="offset points",
                                color=TEXT, fontsize=10, ha="center")

                avg_sleep = sum(sy) / len(sy)
                ax.text(0.99, 1.18, f"Середнє: {avg_sleep:.1f}г",
                        transform=ax.transAxes,
                        color=SLEEP_COLOR, fontsize=12, fontweight="bold",
                        ha="right", va="top")

            ax.set_ylim(0, max(sy or [10]) * 1.3 + 1)
            ax.set_xlim(-0.5, len(x_pos) - 0.5)
            ax.tick_params(colors=TEXT, labelsize=11)
            ax.set_ylabel("😴 Сон", color=SLEEP_COLOR, fontsize=13, fontweight="bold",
                          rotation=0, labelpad=5, ha="right", va="center")
            ax.yaxis.set_label_coords(-0.01, 0.5)
            ax.yaxis.set_tick_params(labelcolor=TEXT, labelsize=11)

            tick_pos  = [i for i in x_pos if i % 3 == 0]
            tick_labs = [date_labels[i] for i in tick_pos]
            ax.set_xticks(tick_pos)
            ax.set_xticklabels(tick_labs, color=TEXT, fontsize=11, rotation=30, ha="right")
            ax.grid(True, color=GRID, linewidth=0.6, alpha=0.7)

        from datetime import datetime as _dtnow3
        _now_label = _dtnow3.utcnow().strftime("%d.%m.%Y %H:%M UTC")
        fig.suptitle(f"Звички за {days} днів  ·  {_now_label}",
                     color=TEXT, fontsize=17, fontweight="bold", y=0.97)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                    facecolor=BG, edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        print(f"[generate_habits_chart] error: {e}")
        return None




_FORCE_REPORT = False  # встановлюється в True для ручного виклику /звіт


def main():
    global _FORCE_REPORT
    force = _FORCE_REPORT
    _FORCE_REPORT = False  # скидаємо після використання

    now = datetime.now(timezone.utc)
    now_local = now + timedelta(hours=2)
    # 3 слоти на годину: :00, :20, :40
    hour_key = _get_report_slot(now_local)
    if hour_key is None:
        if force:
            # При ручному виклику — генеруємо з поточною годиною
            hour_key = now_local.strftime("%Y-%m-%d-%H")
            print(f"=== FORCE report, using hour_key={hour_key} ===")
        else:
            print(f"=== Not in report window (m={now_local.minute}), skipping ===")
            return

    # Захист від дублів — атомарний claim через SHA conflict
    if not force:
        gh_sent, gh_sha = _gh_get_sent()
        if gh_sent is None:
            gh_sent = load_json_file(MAIN_SENT_FILE, default={})
            gh_sha = None

        if gh_sent.get("last_slot") == hour_key:
            print(f"=== Already sent this slot ({hour_key}), skipping ===")
            return
    else:
        gh_sent, gh_sha = {}, None
        print(f"=== FORCE: skipping slot dedup check ===")

    # Атомарно записуємо claim ПЕРЕД відправкою (тільки не в force режимі)
    claim_data = dict(gh_sent)
    claim_data["last_slot"] = hour_key
    claim_data["claimed_at"] = now.isoformat()
    if not force and gh_sha:
        try:
            import base64 as _b64
            gh_token = os.environ.get("GITHUB_TOKEN", "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")
            _url = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/monitor_main_sent.json"
            _content = _b64.b64encode(json.dumps(claim_data, indent=2).encode()).decode()
            _body = json.dumps({
                "message": f"claim slot {hour_key}",
                "content": _content,
                "sha": gh_sha,
                "branch": _GH_DATA_BRANCH,
            }).encode()
            _req = urllib.request.Request(_url, data=_body, headers={
                "Authorization": f"token {gh_token}",
                "Content-Type": "application/json",
                "User-Agent": "morning-report-bot"
            }, method="PUT")
            with urllib.request.urlopen(_req, timeout=8) as _r:
                _resp = json.loads(_r.read())
                gh_sha = _resp.get("content", {}).get("sha", gh_sha)
            print(f"=== Claimed slot {hour_key} ===")
        except urllib.error.HTTPError as _he:
            if _he.code in (409, 422):
                print(f"=== Slot {hour_key} already claimed by another instance, skipping ===")
                return
            print(f"=== GH claim error {_he.code} — proceeding anyway ===")
        except Exception as _ce:
            print(f"=== GH claim error: {_ce} — proceeding anyway ===")
    elif not force:
        # Немає SHA — локальна перевірка (один контейнер)
        _sent = load_json_file(MAIN_SENT_FILE, default={})
        _sent["last_slot"] = hour_key
        save_json_file(MAIN_SENT_FILE, _sent)

    local_time = now_local.strftime("%H:%M")
    local_date = now_local.strftime("%d.%m.%Y")
    weekday = now_local.weekday()
    local_hour = now_local.hour

    is_weekend = weekday >= 5
    include_learning_blocks = True  # крипто/ціни — завжди

    print(f"=== Monitor run at {now.isoformat()} slot={hour_key} (weekend={is_weekend}) ===")

    # ── КРОК 1: СПОЧАТКУ КАЛЕНДАР — він визначає контекст ────────────────────
    cal_events_list, cal_events_text = _get_calendar_context_for_report()
    print(f"Calendar context: {cal_events_text[:80]}")

    # Повний блок календаря для звіту
    try:
        cal_text = get_calendar()
    except Exception as e:
        print(f"get_calendar error: {e}")
        cal_text = "📅 <b>Календар</b>\n⚠️ Помилка"

    # ── КРОК 2: Погода ───────────────────────────────────────────────────────
    try:
        weather_raw = get_weather()
        weather_text = weather_raw  # повний вивід з прогнозом по годинах
    except Exception as e:
        print(f"get_weather error: {e}")
        weather_text = "🌤 <b>Погода</b>\n⚠️ Помилка"
        weather_raw = ""

    # ── КРОК 3: Крипто (тільки якщо не раннє ранкове в будень) ───────────────
    prices_text = None
    if include_learning_blocks:
        try:
            prices_raw = get_prices()
            # Відокремлюємо ETF блок перед форматуванням (щоб не загубити)
            etf_split = prices_raw.split("\n📊 <b>ETF / ІНДЕКСИ / АКЦІЇ</b>", 1)
            crypto_raw = etf_split[0]
            etf_suffix = ("\n\n📊 <b>ETF / ІНДЕКСИ / АКЦІЇ</b>" + etf_split[1]) if len(etf_split) > 1 else ""
            prices_text = (_format_prices_visual(crypto_raw, cal_events_text) or crypto_raw) + etf_suffix
        except Exception as e:
            print(f"get_prices error: {e}")
            prices_text = None

    # ── КРОК 4: Email — завжди включаємо в звіт (незалежно від дня/часу) ──────
    try:
        email_text = get_emails()
    except Exception as e:
        print(f"get_emails error: {e}")
        email_text = None

    # ── КРОК 5: Трафік ───────────────────────────────────────────────────────
    try:
        traffic_text = get_city_traffic()
    except Exception as e:
        print(f"get_traffic error: {e}")
        traffic_text = None

    # ── КРОК 6: Астро ────────────────────────────────────────────────────────
    astro_text = None
    try:
        import sys as _sys, importlib as _importlib
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import astro as _astro_module
        _importlib.reload(_astro_module)
        astro_text = _astro_module.get_natal_transits_short(max_aspects=5)
        print(f"get_astro ok, len={len(astro_text) if astro_text else 0}")
    except Exception as e:
        import traceback as _atb
        print(f"get_astro error: {e}\n{_atb.format_exc()}")

    # ── КРОК 7: AI-підсумок — знає про календар ──────────────────────────────
    try:
        summary_text = get_summary(prices_text or "", weather_raw if 'weather_raw' in dir() else weather_text, cal_text, email_text, astro_text)
    except Exception as e:
        import traceback as _tb
        print(f"get_summary error: {e}\n{_tb.format_exc()}")
        summary_text = ""

    # ── КРОК 8: Calendar-aware AI секція (кожен звіт унікальна порада) ───────
    ai_insight = None
    gemini_key = os.environ.get("GEMINI_API_KEY", "AIzaSyDRXcGERTNILIEDKbmgTKSXUuiwt1oKeGM")
    shift_hint = ""
    weight_hint = ""
    if gemini_key:
        try:
            import uuid as _uuid_r, re as _re_r
            seed = str(_uuid_r.uuid4())[:8]
            h_val = local_hour
            # ── Визначаємо зміну + поточний статус ──────────────────────────
            _sc = _get_current_shift_context(cal_events_text)
            _shift = _sc["shift"]
            _working_now = _sc["is_working_now"]

            shift_hint = ""
            _h_now = (datetime.now(timezone.utc) + timedelta(hours=2)).hour
            if _shift == "early":
                if 6 <= _h_now < 18:
                    shift_hint = "Олег ЗАРАЗ на ранній зміні (06:00–18:00)."
                elif _h_now < 6:
                    shift_hint = "Рання зміна сьогодні (06:00–18:00), ще не почалась."
                else:
                    shift_hint = "Рання зміна сьогодні вже закінчилась (06:00–18:00)."
            elif _shift == "night":
                if _h_now >= 17 or _h_now < 5:
                    shift_hint = "Олег ЗАРАЗ на нічній зміні (17:00–05:00). Зміна вже йде."
                elif 5 <= _h_now < 15:
                    shift_hint = "Нічна зміна щойно закінчилась (17:00–05:00), Олег вдома відпочиває."
                else:
                    shift_hint = "Нічна зміна сьогодні ввечері (17:00–05:00), ще не почалась."
            elif _shift == "after_night":
                shift_hint = "Нічна зміна щойно закінчилась (17:00–05:00), Олег вдома відпочиває."
            else:
                shift_hint = "Сьогодні вихідний/вільний день."

            # Контекст ваги
            weight_hint = ""
            try:
                from storage import load_weight as _lw_r
                wd_r = _lw_r()
                if wd_r:
                    lk = sorted(wd_r.keys())[-1]
                    weight_hint = f"Остання вага: {wd_r[lk]} кг (ціль 78 кг)."
            except: pass

            # Час-специфічна порада з урахуванням зміни
            if _working_now and _shift == "night":
                tip_ctx = "Олег ЗАРАЗ на нічній зміні (17:00–05:00). Дай пораду що допоможе пережити ніч: енергія, концентрація, безпека. НЕ пиши про сон чи відпочинок."
            elif _shift == "night" and 6 <= h_val < 14:
                tip_ctx = "Олег щойно повернувся з нічної зміни. Пора спати і відновитися. Порада про якісний відпочинок після нічної."
            elif _shift == "night" and 14 <= h_val < 17:
                tip_ctx = "Олег прокинувся після нічної зміни. Підготовка до наступної зміни о 17:00. Що важливо зробити за ці 3 години?"
            elif _working_now and _shift == "early":
                tip_ctx = "Олег ЗАРАЗ на ранній зміні (06:00–18:00). Порада для продуктивності на роботі прямо зараз."
            elif _shift == "early" and h_val >= 18:
                tip_ctx = "Рання зміна закінчилась. Вечір після роботи — відпочинок або саморозвиток?"
            elif 5 <= h_val < 9:
                tip_ctx = "Ранок. Дай одну конкретну пораду для успішного старту дня виходячи з календаря та цілей."
            elif 9 <= h_val < 13:
                tip_ctx = "Перша половина дня. Нагадай про найважливіше завдання зараз виходячи з календаря."
            elif 13 <= h_val < 17:
                tip_ctx = "Після обіду. Дай пораду: що зробити для здоров'я або продуктивності в наступні 2 години."
            elif 17 <= h_val < 21:
                tip_ctx = "Вечір. Оціни чи є час для пробіжки або підготовки до завтра з урахуванням зміни."
            else:
                tip_ctx = "Пізній вечір. Що треба підготувати перед сном виходячи з завтрашнього розкладу."

            # Зберемо реальні дані: Strava, звички, вага
            _ai_real_ctx = ""
            try:
                from strava import get_last_activity as _gla
                _la = _gla()
                if _la and _la.get("when") == "сьогодні" and _la.get("distance_km", 0) >= 0.5:
                    _ai_real_ctx += f"Сьогодні вже пробіг: {_la['distance_km']} км за {_la.get('duration_min',0)} хв (темп {_la.get('pace','—')}). "
                else:
                    _ai_real_ctx += "Сьогодні пробіжки ще не було. "
            except: pass
            try:
                from storage import load_habits as _lh_ai
                _hdb = _lh_ai()
                _today_h = _hdb.get(now_local.strftime("%Y-%m-%d"), {})
                _done_h = [k for k,v in _today_h.items() if v is True]
                _ai_real_ctx += f"Виконані звички: {', '.join(_done_h) if _done_h else 'жодної'}. "
            except: pass
            _ai_real_ctx += weight_hint + " "

            ai_prompt = (
                f"Контекст: {shift_hint} {_ai_real_ctx}Календар: {cal_events_text}. {tip_ctx} "
                f"Напиши 3-5 речень українською без вступу, без 'Звичайно', без 'Привіт'. "
                f"ТІЛЬКИ реальні дані — якщо Олег вже пробіг сьогодні, НЕ раджи бігти. "
                f"Конкретні поради для Олега на основі РЕАЛЬНОГО стану зараз. [seed:{seed}]"
            )
            ai_payload = json.dumps({
                "contents": [{"parts": [{"text": ai_prompt}]}],
                "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.7},
            }).encode()
            ai_req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                data=ai_payload, headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(ai_req, timeout=20) as r_ai:
                ai_resp = json.loads(r_ai.read())
            ai_insight = ai_resp["candidates"][0]["content"]["parts"][0]["text"].strip()
            if ai_insight and ai_insight[-1] not in ".!?»":
                ai_insight += "."
        except Exception as e:
            print(f"ai_insight error: {e}")

    # ── СКЛАДАЄМО ЗВІТ ────────────────────────────────────────────────────────
    import re as _re_rep
    import hashlib as _hsh_rep

    _today_rep = now_local.strftime("%Y-%m-%d")
    _yest_rep  = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")

    def _sparkline(vals, width=10):
        """Текстовий спарклайн зі значень (list[float|None])."""
        _chars = "▁▂▃▄▅▆▇█"
        clean = [v for v in vals if v is not None]
        if len(clean) < 2:
            return "─" * width
        lo, hi = min(clean), max(clean)
        rng = hi - lo or 1
        result = ""
        for v in vals[-width:]:
            if v is None:
                result += "·"
            else:
                idx = int((v - lo) / rng * (len(_chars) - 1))
                result += _chars[idx]
        return result

    def _pct_bar(pct, width=10, fill="●", empty="○"):
        """Прогресбар від 0..100 — крапковий стиль."""
        filled = int(pct / 100 * width)
        return fill * filled + empty * max(0, width - filled)

    def _section_header(emoji, title):
        """Заголовок секції — емодзі окремо (великий), жирний текст."""
        return f"{emoji}\n<b>{title}</b>"

    # ── Динамічний заголовок ───────────────────────────────────────────────────
    header = _build_report_header(now_local, hour_key, cal_text)
    parts = [header]

    # ── ДЕНЬ-РЕЙТИНГ ⭐ ────────────────────────────────────────────────────────
    try:
        def _calc_day_score():
            score = 0
            breakdown = {}

            # Сон (25 балів): з QWatch
            try:
                qw_all = storage.load("qwatch_data.json", default={})
                qw = qw_all.get(_today_rep) or qw_all.get(_yest_rep) or {}
                sleep_min = qw.get("sleep_total_min", 0) or 0
                sleep_h = sleep_min / 60
                if sleep_h >= 7.5:   s = 25
                elif sleep_h >= 6.5: s = 20
                elif sleep_h >= 5.5: s = 13
                elif sleep_h >= 4.5: s = 7
                else:                s = 0
                score += s
                breakdown["Сон"] = s
                # HRV (15 балів)
                hrv = qw.get("hrv", 0) or 0
                if hrv >= 60:   h = 15
                elif hrv >= 45: h = 10
                elif hrv >= 30: h = 5
                else:           h = 0
                score += h
                breakdown["HRV"] = h
            except: pass

            # Звички (20 балів): всі рівноцінні
            try:
                hd = storage.load_habits()
                today_h = hd.get(_today_rep, {})
                HAB_IDS = ["shower","run","water","tea","sauna","spray"]
                done = sum(1 for k in HAB_IDS if today_h.get(k) is True)
                total = len(HAB_IDS)
                h_pts = round(done / total * 20) if total else 0
                score += h_pts
                breakdown["Звички"] = h_pts
            except: pass

            # Кроки (15 балів)
            try:
                qw_all2 = storage.load("qwatch_data.json", default={})
                qw2 = qw_all2.get(_today_rep) or qw_all2.get(_yest_rep) or {}
                steps = qw2.get("steps", 0) or 0
                if steps >= 12000:  k = 15
                elif steps >= 8000: k = 12
                elif steps >= 5000: k = 7
                elif steps >= 2000: k = 3
                else:               k = 0
                score += k
                breakdown["Кроки"] = k
            except: pass

            # Ліки (10 балів)
            try:
                mdb = storage.load_meds()
                if mdb.get(_today_rep) is True:
                    score += 10
                    breakdown["Ліки"] = 10
                else:
                    breakdown["Ліки"] = 0
            except: pass

            # Біг сьогодні або вчора (10 балів)
            try:
                from strava import get_last_activity as _gla_sc
                _lr = _gla_sc()
                if _lr and _lr.get("when") in ("сьогодні", "вчора"):
                    score += 10
                    breakdown["Біг"] = 10
                else:
                    breakdown["Біг"] = 0
            except: pass

            return min(score, 100), breakdown

        _score, _breakdown = _calc_day_score()

        # Зірки: 5 зірок, кожна = 20 балів
        _full = _score // 20
        _half = 1 if (_score % 20) >= 10 else 0
        _empty = 5 - _full - _half
        _stars = "⭐" * _full + ("✨" if _half else "") + "☆" * _empty

        # Тренд vs вчора
        _trend_str = ""
        try:
            _scores_hist = storage.load("day_scores.json", default={})
            _yest_score = _scores_hist.get(_yest_rep)
            if _yest_score is not None:
                _diff = _score - int(_yest_score)
                _trend_str = f"  {'↑' if _diff >= 0 else '↓'}{abs(_diff):+d} vs вчора".replace("+-","+")
                _trend_str = f"  {'↑+' if _diff >= 0 else '↓'}{abs(_diff)} vs вчора"
            # Зберігаємо сьогоднішній скор
            _scores_hist[_today_rep] = _score
            _scores_hist = {k: v for k, v in sorted(_scores_hist.items())[-60:]}
            storage.save("day_scores.json", _scores_hist)
        except: pass

        _score_line = f"⚡ <b>День: {_stars} {_score}/100</b>{_trend_str}"
        parts.insert(1, _score_line)   # позиція 1, AI вставиться перед ним пізніше
        print(f"[day_score] {_score}/100 — {_breakdown}")
    except Exception as _dse:
        print(f"[day_score] error: {_dse}")

    # ── Блок 1: ПОГОДА — розширений ───────────────────────────────────────────
    try:
        _wl = weather_text.lower() if weather_text else ""
        # Іконка умов
        if "гроза" in _wl: _w_icon = "⛈️"
        elif "злива" in _wl or "сильний дощ" in _wl: _w_icon = "🌧️"
        elif "дощ" in _wl: _w_icon = "🌦️"
        elif "сніг" in _wl: _w_icon = "❄️"
        elif "туман" in _wl or "мряка" in _wl: _w_icon = "🌫️"
        elif "ясно" in _wl or "сонячно" in _wl: _w_icon = "☀️"
        elif "хмарно" in _wl: _w_icon = "☁️"
        else: _w_icon = "🌤️"

        _temp_m  = _re_rep.search(r"([-−]?\d+)[°℃C]", weather_text or "")
        _feel_m  = _re_rep.search(r"відчув[^\d]*([-−]?\d+)", weather_text or "", _re_rep.I)
        _hum_m   = _re_rep.search(r"вологість[:\s]*(\d+)%", weather_text or "", _re_rep.I)
        _wind_m  = _re_rep.search(r"вітер[:\s]*([\d.]+)", weather_text or "", _re_rep.I)
        _uv_m    = _re_rep.search(r"УФ[^:]*:\s*(\d+)", weather_text or "", _re_rep.I)
        _rain_m  = _re_rep.search(r"([\d.]+)\s*мм", weather_text or "")

        _temp  = int(_temp_m.group(1).replace("−","-")) if _temp_m else None
        _feel  = int(_feel_m.group(1).replace("−","-")) if _feel_m else _temp
        _hum   = int(_hum_m.group(1)) if _hum_m else None
        _wind  = float(_wind_m.group(1)) if _wind_m else None
        _uv    = int(_uv_m.group(1)) if _uv_m else None
        _rain  = float(_rain_m.group(1)) if _rain_m else None

        # Комфортний індекс
        _comfort = ""
        if _temp is not None:
            if _temp < 0: _comfort = "🥶 Мороз — одягайся тепло!"
            elif _temp < 8: _comfort = "🧥 Прохолодно — куртка обов'язкова"
            elif _temp < 16: _comfort = "😊 Свіжо — легка куртка"
            elif _temp < 24: _comfort = "👌 Комфортно — ідеально для прогулянки"
            elif _temp < 30: _comfort = "☀️ Тепло — сонцезахисний крем"
            else: _comfort = "🥵 Спека — пий більше води!"

        # Поради
        _tips = []
        if "дощ" in _wl or "злива" in _wl: _tips.append("☂️ парасолька")
        if "гроза" in _wl: _tips.append("🏠 краще вдома")
        if _wind and _wind > 10: _tips.append(f"💨 вітер {_wind:.0f} м/с")
        if _uv and _uv >= 6: _tips.append(f"🕶️ УФ {_uv} — захист")
        if _rain and _rain > 5: _tips.append(f"🌧️ {_rain:.1f} мм дощу")

        # Вологість — прогресбар
        _hum_bar = _pct_bar(_hum, 8) if _hum else ""

        _weather_block = _section_header(_w_icon, "ПОГОДА — Košice") + "\n"
        if _temp is not None:
            _feel_str = f"  <i>(відчув. {_feel}°)</i>" if _feel != _temp else ""
            _weather_block += f"🌡️ <b>{_temp}°C</b>{_feel_str}"
            if _hum: _weather_block += f"   💧 {_hum}% {_hum_bar}"
            if _wind: _weather_block += f"   🌬️ {_wind:.0f} м/с"
            _weather_block += "\n"
        if _comfort:
            _weather_block += f"<i>{_comfort}</i>\n"
        if _tips:
            _weather_block += f"<i>{'  ·  '.join(_tips)}</i>\n"

        # Прогноз на сьогодні — витягуємо рядки з годинами
        # ВАЖЛИВО: шукаємо рядок що починається з <code>ЧЧ:ХХ (або просто ЧЧ:ХХ)
        # щоб не зрізати відкриваючий <code> тег і не отримати orphan </code>
        _forecast_lines = _re_rep.findall(r"(?:<code>)?\d{2}:\d{2}[^\n]+", weather_text or "")
        # Стрипуємо будь-які теги з витягнутих рядків — показуємо як plain в блоці
        _today_fc_raw = [_re_rep.sub(r'<[^>]+>', '', l) for l in _forecast_lines if "00:" not in l]
        _today_fc = _today_fc_raw[:5]
        if _today_fc:
            _weather_block += "\n📅 <b>Прогноз сьогодні:</b>\n"
            _weather_block += "  ".join(_today_fc[:4])

        # Прогноз на завтра — витягуємо з weather_text
        if weather_text and "Завтра:" in weather_text:
            import re as _re_tmr
            _tmr_match = _re_tmr.search(r"<b>Завтра:</b>[^\n]+", weather_text)
            if _tmr_match:
                _weather_block += f"\n\n{_tmr_match.group(0)}"
            # Погодинний прогноз на завтра — рядок після "Завтра:" через \n
            _tmr_idx = weather_text.find("Завтра:")
            if _tmr_idx != -1:
                _tmr_after = weather_text[_tmr_idx:]
                _lines_tmr = _tmr_after.split("\n")
                # Шукаємо рядок з <code>
                for _tl in _lines_tmr[1:3]:
                    if "<code>" in _tl and _tl.strip():
                        _weather_block += f"\n{_tl.strip()}"
                        break

        parts.append(_weather_block)
    except Exception as _e_wb:
        parts.append(weather_text)
        print(f"weather block format error: {_e_wb}")

    # ── Блок 2: ТРАФІК ────────────────────────────────────────────────────────
    if traffic_text:
        parts.append(_section_header("🚦", "ТРАФІК — Košice") + "\n" + "\n".join(traffic_text.split("\n")[1:]) if "\n" in traffic_text else _section_header("🚦", "ТРАФІК") + "\n" + traffic_text)

    # ── Блок 3: КРИПТО — спарклайн + ринок ───────────────────────────────────
    if prices_text:
        try:
            import storage as _st_c
            _pdata = _st_c.load("prices_history.json") or {}

            _up = prices_text.count("🔺")
            _dn = prices_text.count("🔻")
            if _up > _dn + 1:   _mkt = "🟢 БИЧАЧИЙ 🚀"
            elif _dn > _up + 1: _mkt = "🔴 ВЕДМЕЖИЙ 📉"
            else:                _mkt = "🟡 НЕЙТРАЛЬНИЙ 〰️"

            _crypto_block = _section_header("💰", f"КРИПТО  ·  {_mkt}") + "\n"

            for _coin in ["BTC", "ETH", "AVAX", "ONDO"]:
                _row_m = _re_rep.search(r"[^\n]*\b" + _coin + r"\b[^\n]*", prices_text)
                if not _row_m: continue
                _row = _row_m.group(0)
                _pr_m  = _re_rep.search(r"\$([\d,]+(?:\.\d+)?)", _row)
                _pct_m = _re_rep.search(r"([+\-−][\d.]+)%", _row)
                if not _pr_m: continue
                _price_str = _pr_m.group(1)
                _pct_val = float(_pct_m.group(1).replace("−","-")) if _pct_m else 0
                _arrow = "🔺" if _pct_val > 0.1 else ("🔻" if _pct_val < -0.1 else "➡️")
                _pct_str = (("+" if _pct_val > 0 else "") + f"{_pct_val:.2f}%") if _pct_m else ""

                # Спарклайн з history
                _hist = _pdata.get(_coin, [])
                _spark = ""
                if len(_hist) >= 4:
                    _spark = f"  <code>{_sparkline(_hist[-12:], 8)}</code>"

                _crypto_block += f"{_arrow} <b>{_coin}</b> <code>${_price_str}</code>  {_pct_str}{_spark}\n"

            parts.append(_crypto_block.rstrip())

            # Графік крипто — кожні 4 год (o 8, 12, 16, 20)
            if now_local.hour in (8, 12, 16, 20) and now_local.minute < 35:
                try:
                    _cchart = generate_crypto_trend_chart(30)
                    if _cchart:
                        parts.append({"photo": _cchart, "caption": "📈 Тренд 30д | BTC ETH AVAX ONDO"})
                except Exception as _e_cc:
                    print(f"crypto chart error: {_e_cc}")

            # ── ETF / ІНДЕКСИ ── завжди після крипто
            try:
                _etf_block = _get_etf_prices()
                if _etf_block:
                    parts.append(_etf_block)
            except Exception as _e_etf:
                print(f"etf block error: {_e_etf}")
        except Exception as _e_cb:
            parts.append(prices_text)
            print(f"crypto block error: {_e_cb}")

    # ── Блок 4: КАЛЕНДАР ──────────────────────────────────────────────────────
    parts.append(_section_header("📅", "КАЛЕНДАР") + "\n" + "\n".join(cal_text.split("\n")[1:]) if isinstance(cal_text, str) and "\n" in cal_text else _section_header("📅", "КАЛЕНДАР") + "\n" + str(cal_text))

    # ── SPLIT: тут ділимо на 2 повідомлення ──────────────────────────────────
    parts.append("SPLIT_HERE")

    # ── Блок 5: ЗДОРОВ'Я — з прогресбарами і спарклайном ─────────────────────
    try:
        _health_lines = [_section_header("💪", "ЗДОРОВ'Я")]
        import storage as _st_h

        # Вага — таблиця останніх 7 ДНІВ (з прочерком якщо нема запису)
        try:
            _wd = _st_h.load("weight_data.json") or _st_h.load_weight() or {}
            if _wd:
                _all_w_keys = sorted(_wd.keys())
                _last_w_key = _all_w_keys[-1] if _all_w_keys else None
                _last_w = _wd.get(_last_w_key) if _last_w_key else None
                if _last_w:
                    _diff_goal = round(_last_w - 78.0, 1)
                    _all_w_vals = [_wd[k] for k in _all_w_keys if _wd.get(k)]
                    _delta_w = round(_all_w_vals[-1] - _all_w_vals[-2], 1) if len(_all_w_vals) >= 2 else 0
                    _d_icon = "📈" if _delta_w > 0.1 else ("📉" if _delta_w < -0.1 else "➡️")
                    _goal_str = f"до 78 кг: <b>{_diff_goal:+.1f} кг</b>" if _diff_goal > 0 else "🏆 <b>Ціль 78 кг досягнута!</b>"
                    _w_header = f"⚖️ <b>{_last_w} кг</b>  {_d_icon} {_delta_w:+.1f} кг  |  {_goal_str}"
                    # Останні 7 ДНІВ з прочерком де нема запису
                    _w_rows = []
                    for _di in range(6, -1, -1):
                        _dk = (now_local - timedelta(days=_di)).strftime("%Y-%m-%d")
                        _dv = _wd.get(_dk)
                        _dlabel = (now_local - timedelta(days=_di)).strftime("%d.%m")
                        _dstr = f"<b>{_dv} кг</b>" if _dv else "—"
                        _w_rows.append(f"  {_dlabel}  {_dstr}")
                    _health_lines.append(_w_header + "\n" + "\n".join(_w_rows))
        except Exception as _e_w: pass

        # Кроки — таблиця останніх 7 днів
        # Кроки — з QWatch (основне), fallback StepsApp
        try:
            _qw_s = _st_h.load("qwatch_data.json", default={})
            _st_today_n = (_qw_s.get(_today_rep) or {}).get("steps", 0) or 0
            _st_yest_n  = (_qw_s.get(_yest_rep) or {}).get("steps", 0) or 0
            _show_steps = _st_today_n if _st_today_n > 0 else _st_yest_n
            _steps_label = "Сьогодні" if _st_today_n > 0 else "Вчора"
            _st_icon = "✅" if _show_steps >= 8000 else ("🟡" if _show_steps >= 5000 else "🔴")
            _st_header = f"👟 Кроки ({_steps_label}): <b>{_show_steps:,}</b> {_st_icon}"
            # Таблиця 7 ДНІВ з прочерком
            _st_rows = []
            for _di in range(6, -1, -1):
                _dk = (now_local - timedelta(days=_di)).strftime("%Y-%m-%d")
                _dlabel = (now_local - timedelta(days=_di)).strftime("%d.%m")
                _sv = (_qw_s.get(_dk) or {}).get("steps", 0) or 0
                if _sv:
                    _sicon = "✅" if _sv >= 8000 else ("🟡" if _sv >= 5000 else "🔴")
                    _st_rows.append(f"  {_dlabel}  <b>{_sv:,}</b> {_sicon}")
                else:
                    _st_rows.append(f"  {_dlabel}  —")
            _health_lines.append(_st_header + "\n" + "\n".join(_st_rows))
        except Exception: pass

        # Ліки
        try:
            from storage import load_meds as _lmeds_h
            _meds_db = _lmeds_h()
            _taken = _meds_db.get(_today_rep)
            if _taken:
                _health_lines.append("💊 Armolopid: ✅ <b>прийнято</b>")
            else:
                _health_lines.append("💊 Armolopid: ❌ <b>не відмічено</b> — прийняв?")
        except Exception: pass

        # Звички сьогодні — рядок іконок
        try:
            from storage import load_habits as _lhab
            _habs = _lhab()
            _hentry = _habs.get(_today_rep, {}) or {}
            _HLIST = [("shower","🚿","Душ"),("run","🏃","Біг"),("water","💧","Вода"),("tea","🍵","Чай"),("sauna","🧖","Сауна"),("spray","💈","Спрей")]
            _hab_line = "  ".join(
                f"{ico} {name} {'✅' if _hentry.get(k) is True else '⬜'}"
                for k, ico, name in _HLIST
            )
            _done_h = sum(1 for k,_,__ in _HLIST if _hentry.get(k) is True)
            _health_lines.append(f"🎯 Звички: {_hab_line}  <b>{_done_h}/6</b>")
        except Exception: pass

        if len(_health_lines) > 1:
            parts.append("\n".join(_health_lines))
    except Exception as _e_health:
        print(f"health block error: {_e_health}")

    # ── Блок 6: STRAVA ────────────────────────────────────────────────────────
    try:
        import sys as _sys_strava
        _sys_strava.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from strava import format_strava_block
        _strava_text = format_strava_block()
        if _strava_text:
            parts.append(_section_header("🏃", "БІГОВИЙ ТРЕКЕР") + "\n" + "\n".join(_strava_text.split("\n")[1:]) if "\n" in _strava_text else _section_header("🏃", "БІГОВИЙ ТРЕКЕР") + "\n" + _strava_text)
    except Exception as _e_strava:
        print(f"strava block error: {_e_strava}")

    # ── Комбінований дашборд (звички + вага + біг + фінанси) — ОДНА картинка ─
    try:
        from charts import plot_combined_dashboard as _plot_combined
        print("[combined chart] generating...", flush=True)
        _combined_chart = _plot_combined()
        if _combined_chart:
            parts.append({"photo": _combined_chart, "caption": f"📊 Дашборд 2026 — до {now_local.strftime('%d.%m.%Y')}"})
            print(f"[combined chart] done, {len(_combined_chart)} bytes", flush=True)
        else:
            print("[combined chart] returned None", flush=True)
    except Exception as _e_combined:
        import traceback as _tb_comb
        print(f"combined chart error: {_e_combined}\n{_tb_comb.format_exc()}", flush=True)

    # ── Блок 7: КУРС ВАЛЮТ ────────────────────────────────────────────────────
    try:
        _currency_text = get_currency_rates()
        if _currency_text:
            parts.append(_section_header("💱", "КУРС ВАЛЮТ") + "\n" + "\n".join(_currency_text.split("\n")[1:]) if "\n" in _currency_text else _section_header("💱", "КУРС ВАЛЮТ") + "\n" + _currency_text)
    except Exception as _e_curr:
        print(f"currency rates error: {_e_curr}")

    # ── Блок 8: ПОРТФЕЛЬ — з динамікою ───────────────────────────────────────
    try:
        import sys as _sys_pf
        _sys_pf.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from portfolio import format_portfolio_block
        _pf_text = format_portfolio_block(short=True)
        if _pf_text:
            # Збагачуємо заголовок портфелю
            _pf_lines = _pf_text.split("\n")
            _pf_header = _section_header("💼", "ПОРТФЕЛЬ")
            # шукаємо суму і P&L
            _total_m = _re_rep.search(r"\$([\d,]+)", _pf_text)
            _pnl_m   = _re_rep.search(r"P&L[:\s]*([+\-]?\$[\d,]+)", _pf_text, _re_rep.I)
            _day_m   = _re_rep.search(r"За 24г[:\s]*([+\-]?\$[\d,]+)", _pf_text, _re_rep.I)
            if _total_m:
                _pf_header += f"  💰 <b>${_total_m.group(1)}</b>"
            if _day_m:
                _day_v = _day_m.group(1)
                _day_icon = "📈" if "+" in _day_v else "📉"
                _pf_header += f"  {_day_icon} {_day_v} сьогодні"
            parts.append(_pf_header + "\n" + "\n".join(_pf_lines[1:] if len(_pf_lines) > 1 else _pf_lines))
    except Exception as _e_pf:
        print(f"portfolio block error: {_e_pf}")

    # ── Вага + звички за місяць — КОЖЕН звіт ─────────────────────────────────
    # ── Тижневий підсумок — неділя о 20:20-20:29 ──────────────────────────────
    if now_local.weekday() == 6 and now_local.hour == 20 and 20 <= now_local.minute <= 29:
        try:
            print(f"[charts] generating weekly dashboard...", flush=True)
            from charts import plot_weekly_dashboard as _plot_weekly
            _wchart = _plot_weekly()
            print(f"[charts] weekly dashboard: {len(_wchart) if _wchart else 0} bytes", flush=True)
            if _wchart:
                parts.append({"photo": _wchart, "caption": "📅 Тижневий підсумок"})
        except Exception as _e_weekly:
            import traceback as _tb_weekly
            print(f"weekly dashboard error: {_e_weekly}\n{_tb_weekly.format_exc()}", flush=True)

    # ── Місячний підсумок — останній день місяця о 20:xx ──────────────────────
    import calendar as _cal_check
    _, _last_day = _cal_check.monthrange(now_local.year, now_local.month)
    if now_local.day == _last_day and now_local.hour == 20:
        # monthly_dashboard і run_month вже відправлені вище з підписом місяця
        print(f"[charts] last day of month — monthly summary already sent above", flush=True)

    # Блок 5: Email — збираємо окремо, надсилаємо ПІСЛЯ основного звіту
    email_parts = []
    if email_text:
        if isinstance(email_text, dict) and email_text.get("__email_block__"):
            email_parts.append(email_text["header"])
            _all_items = email_text.get("items", [])
            _remaining = max(0, len(_all_items) - 7)
            for _em in _all_items[:7]:
                _s   = _em["subject"]
                _snd = _em["sender"]
                _uid = _em["uid"]
                _u   = _em["unread"]
                _status = "🔴 <b>НОВЕ</b>" if _u else "✉️"
                _s_low = _s.lower() + _snd.lower()
                if any(k in _s_low for k in ["invoice","інвойс","рахунок","payment","оплат"]):
                    _cat = "💰"
                elif any(k in _s_low for k in ["security","безпек","password","пароль","alert","verify"]):
                    _cat = "🔐"
                elif any(k in _s_low for k in ["order","замовлен","delivery","доставк","shipment"]):
                    _cat = "📦"
                elif any(k in _s_low for k in ["meeting","зустріч","calendar","invite","запрошен"]):
                    _cat = "📅"
                elif any(k in _s_low for k in ["job","робот","vacancy","вакансі","career"]):
                    _cat = "💼"
                else:
                    _cat = "📩"
                _text = (
                    f"{_cat} {_status}\n"
                    f"📌 <b>{esc(_s[:60])}</b>\n"
                    f"👤 {esc(_snd[:50])}"
                )
                _keyboard = {"inline_keyboard": [
                    [
                        {"text": "📖 Описати лист", "callback_data": f"email_describe_{_uid}"},
                        {"text": "📅 В календар",   "callback_data": f"email_cal_{_uid}"},
                    ],
                    [
                        {"text": "📥 Залишити",     "callback_data": f"email_keep_{_uid}"},
                        {"text": "🗑 Видалити",     "callback_data": f"email_delete_{_uid}"},
                    ]
                ]}
                email_parts.append({"email_msg": True, "text": _text, "keyboard": _keyboard})
            if _remaining > 0:
                email_parts.append(f"📬 <i>і ще {_remaining} {'лист' if _remaining == 1 else 'листи' if _remaining < 5 else 'листів'} у вхідних</i>")
        else:
            email_parts.append(email_text)

    # Блок 6: Астро — додається в кожен звіт
    if astro_text:
        parts.append(astro_text)
        print(f"astro: додано в звіт (slot={hour_key})")

    # Блок 7: AI-підсумок
    if summary_text:
        parts.append(summary_text)

    # Блок 8: Calendar-aware AI порада (нова, унікальна кожен раз)
    if ai_insight:
        parts.append(f"🤖 <b>AI-порада</b>\n<i>{esc(ai_insight)}</i>")

    # Блок: Список покупок (тільки незакуплені, тільки якщо є)
    try:
        import shopping as _sh_rep
        _uncompleted = _sh_rep.get_uncompleted()
        if _uncompleted:
            def _shop_emoji(item_text):
                t = item_text.lower()
                if any(k in t for k in ["молоко","кефір","йогурт","сир","масло","яйц","вершк","сметан"]):
                    return "🥛"
                if any(k in t for k in ["хліб","булк","батон","рогалик","тіст","борошн"]):
                    return "🍞"
                if any(k in t for k in ["м'яс","куряч","свинин","яловичин","філе","фарш","ковбас","шинк"]):
                    return "🥩"
                if any(k in t for k in ["риб","лосос","тунец","оселедец","морепродукт"]):
                    return "🐟"
                if any(k in t for k in ["яблук","банан","апельсин","лимон","груш","виноград","полуниц","фрукт"]):
                    return "🍎"
                if any(k in t for k in ["помідор","огірок","перець","цибул","часник","морков","картопл","броккол","салат","овоч"]):
                    return "🥦"
                if any(k in t for k in ["вод","сік","чай","кав","напій","пиво","вино"]):
                    return "🥤"
                if any(k in t for k in ["шоколад","цукерк","торт","печив","солодощ","мед","варен"]):
                    return "🍫"
                if any(k in t for k in ["гречк","рис","макарон","паст","крупа","вівсян","вермішель"]):
                    return "🌾"
                if any(k in t for k in ["мило","шампун","гель","зубн","туалет","паперов","косметик","крем","дезодорант"]):
                    return "🧴"
                if any(k in t for k in ["ліки","таблетк","вітамін","аптек","препарат","armolopid","армолопід"]):
                    return "💊"
                if any(k in t for k in ["спорт","протеїн","добавк","bcaa","омег"]):
                    return "💪"
                return "🛍️"
            _shop_lines = "\n".join(f"{_shop_emoji(x)} {x}" for x in _uncompleted)
            parts.append(f"🛒 <b>Список покупок</b>\n{_shop_lines}")
    except Exception as _sh_err:
        print(f"shopping in report error: {_sh_err}")

    # Вихідний блок — тільки якщо Олег не на нічній зміні зараз
    _sc_main = _get_current_shift_context(cal_events_text)
    if is_weekend and not include_learning_blocks and not _sc_main["is_working_now"]:
        parts.append("💤 <i>Вихідний — крипто/пошта з 11:00</i>")

    # ── AI-брифінг: генерується з ПОВНИХ даних звіту ─────────────────────────
    if gemini_key:
        _ai_briefing = None
        for _attempt_b in range(3):  # 3 спроби
            try:
                import uuid as _uuid_b
                _seed_b = str(_uuid_b.uuid4())[:8]

                # Збираємо всі текстові частини звіту (без фото)
                _all_report_text_parts = []
                for _p in parts:
                    if isinstance(_p, str) and _p != "SPLIT_HERE":
                        _all_report_text_parts.append(_p)
                # Додаємо email окремо якщо є
                if email_text:
                    if isinstance(email_text, dict):
                        _all_report_text_parts.append(email_text.get("header", ""))
                    elif isinstance(email_text, str):
                        _all_report_text_parts.append(email_text)
                # Додаємо ПОВНИЙ астро звіт окремо
                _astro_full_ctx = ""
                try:
                    import astro as _astro_brief_mod, importlib as _il_b
                    _il_b.reload(_astro_brief_mod)
                    _astro_full_ctx = _astro_brief_mod.get_astro_report()
                except Exception as _e_ab:
                    _astro_full_ctx = astro_text or ""

                _full_report_ctx = "\n\n".join(_all_report_text_parts)[:15000]

                _brief_prompt = (
                    f"Ти — персональний тренер, асистент, нутриціолог і астролог Олега Новосадова (Кошіце, Словаччина).\n"
                    f"Зараз {now_local.strftime('%H:%M')}, {now_local.strftime('%d.%m.%Y')}.\n"
                    f"{shift_hint}\n\n"
                    f"=== ПОВНИЙ ЗВІТ БОТА (ЗДОРОВ'Я / КРИПТО / ПОГОДА / ПОШТА / КАЛЕНДАР) ===\n"
                    f"{_full_report_ctx}\n"
                    f"=== АСТРОЛОГІЧНИЙ ЗВІТ ===\n"
                    f"{_astro_full_ctx[:2000]}\n"
                    f"==========================================\n\n"
                    f"Прочитай ВСЕ і напиши живий персональний брифінг — РІВНО 100 речень.\n"
                    f"Починай з 'Олег,'. БЕЗ вступів, БЕЗ 'Ось', БЕЗ 'Звичайно', БЕЗ нумерації, БЕЗ заголовків.\n"
                    f"Пиши суцільним живим текстом, як близький друг-тренер який знає про тебе все.\n\n"
                    f"ОБОВ'ЯЗКОВО охопи ВСІ ці теми (кожна — 2-3 речення):\n"
                    f"• Поточний момент: зміна/вільний час, що відбувається зараз\n"
                    f"• Вага та тіло: точна цифра, тенденція за тиждень, відстань до цілі 79 кг\n"
                    f"• Активність сьогодні: біг/кроки — є чи немає, конкретна оцінка\n"
                    f"• Звички: скільки з 6 виконано, що пропущено, похвала або м'який докір\n"
                    f"• Ліки Armolopid: статус прийому, нагадай якщо не підтверджено\n"
                    f"• Харчування та вода: що вже зробив, що варто зробити\n"
                    f"• Крипто та фінанси: BTC/ETH/AVAX/ONDO — конкретні ціни і рухи, стан портфелю\n"
                    f"• Погода в Кошіце: що надягнути, чи варто виходити\n"
                    f"• Календар: найближчі події, що критично не пропустити\n"
                    f"• Пошта: є щось від Мароша Сівака або інші важливі листи\n"
                    f"• Астро сьогодні: місяць, планети, транзити — як впливають на день Олега\n"
                    f"• Астро-порада: що сприятливо/несприятливо робити зараз за зірками\n"
                    f"• Ціль на день: 1 найважливіша конкретна дія для схуднення АБО фінансів АБО нової роботи\n"
                    f"• Мотивація: щира і конкретна, без банальностей і кліше\n\n"
                    f"ПРАВИЛА: лише реальні дані зі звітів (числа, ціни, імена, час, градуси планет).\n"
                    f"Якщо даних по темі немає — 1 коротке речення і далі. Загальний тон: енергійно, по-людськи, з турботою.\n"
                    f"ЯКЩО зміна вже йде — 'ти зараз на зміні', не 'чекає зміни'. [seed:{_seed_b}]"
                )
                _brief_payload = json.dumps({
                    "contents": [{"parts": [{"text": _brief_prompt}]}],
                    "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.8},
                }).encode()
                _brief_req = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    data=_brief_payload, headers={"Content-Type": "application/json"}, method="POST"
                )
                with urllib.request.urlopen(_brief_req, timeout=45) as _r_b:
                    _brief_resp = json.loads(_r_b.read())
                _ai_briefing = _brief_resp["candidates"][0]["content"]["parts"][0]["text"].strip()
                if _ai_briefing and _ai_briefing[-1] not in ".!?»":
                    _ai_briefing += "."
                print(f"[briefing] OK (attempt {_attempt_b+1}) — {len(_ai_briefing)} chars")
                break  # успіх — виходимо з циклу
            except Exception as _e_b:
                print(f"ai_briefing error (attempt {_attempt_b+1}): {_e_b}")
                if _attempt_b < 2:
                    import time as _t_b; _t_b.sleep(3)

        # Вставляємо на позицію 1 — завжди перед score (позиція 0 = заголовок)
        if _ai_briefing:
            parts.insert(1, f"🤖 <i>{esc(_ai_briefing)}</i>")
        else:
            parts.insert(1, "🤖 <i>AI-брифінг недоступний</i>")

    # ── Надсилаємо звіт рівно 2 повідомленнями ───────────────────────────────
    # Повідомлення 1: заголовок + погода + трафік + крипто + ETF + курс + календар
    # Повідомлення 2: здоров'я + біг + портфоліо + пошта + астро + AI + підсумок
    # Фото збираємо окремо в album, текст об'єднуємо в 2 повідомлення.
    # "SPLIT_HERE" — маркер між двома текстовими повідомленнями.
    import time as _time_main
    import requests as _req_send
    import io as _io_send
    import json as _json_send

    # Витягуємо всі фото з parts в окремий список
    photo_parts = [p for p in parts if isinstance(p, dict) and "photo" in p]
    parts_no_photo = [p for p in parts if not (isinstance(p, dict) and "photo" in p)]

    # Ділимо по явному маркеру SPLIT_HERE
    _split_idx = next((i for i, p in enumerate(parts_no_photo) if p == "SPLIT_HERE"), None)
    if _split_idx is not None:
        parts_1 = parts_no_photo[:_split_idx]
        parts_2 = parts_no_photo[_split_idx + 1:]
    else:
        mid = len(parts_no_photo) // 2
        parts_1 = parts_no_photo[:mid]
        parts_2 = parts_no_photo[mid:]

    def _send_album(photos):
        """Надсилає кожне фото ОКРЕМИМ повідомленням — максимальний розмір на екрані."""
        if not photos:
            return
        for i, p in enumerate(photos):
            try:
                caption = p.get("caption", "")
                files = {"photo": (f"chart{i}.png", _io_send.BytesIO(p["photo"]), "image/png")}
                r = _req_send.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                    data={"chat_id": TELEGRAM_CHAT, "caption": caption, "parse_mode": "HTML"},
                    files=files,
                    timeout=60,
                )
                print(f"[photo] sent photo {i+1}/{len(photos)}: {r.status_code}", flush=True)
                _time_main.sleep(0.5)
            except Exception as e:
                print(f"[photo] error photo {i}: {e}", flush=True)

    MAX_MSG = 4090
    ok = True

    def _send_parts_as_one(plist):
        """Об'єднує текстові секції в одне повідомлення (або мінімум шматків)."""
        nonlocal ok
        current = ""

        def _flush():
            nonlocal current, ok
            if current:
                if not send_telegram(current):
                    ok = False
                current = ""
                _time_main.sleep(0.5)

        for sec in plist:
            if isinstance(sec, dict) and sec.get("email_msg"):
                _flush()
                _send_telegram_text_with_keyboard(sec["text"], sec["keyboard"])
                _time_main.sleep(0.5)
                continue
            SEP = "\n\n"
            candidate = current + (SEP if current else "") + sec
            if len(candidate) <= MAX_MSG:
                current = candidate
            else:
                _flush()
                if len(sec) > MAX_MSG:
                    chunk = ""
                    for line in sec.split("\n"):
                        c2 = chunk + ("\n" if chunk else "") + line
                        if len(c2) <= MAX_MSG:
                            chunk = c2
                        else:
                            if chunk:
                                if not send_telegram(chunk):
                                    ok = False
                                _time_main.sleep(0.5)
                            chunk = line
                    current = chunk
                else:
                    current = sec
        _flush()

    print(f"[report] sending part 1 ({len(parts_1)} sections)", flush=True)
    _send_parts_as_one(parts_1)
    _time_main.sleep(0.8)
    print(f"[report] sending part 2 ({len(parts_2)} sections)", flush=True)
    _send_parts_as_one(parts_2)

    # Album з усіма фото після тексту
    if photo_parts:
        _time_main.sleep(0.8)
        print(f"[report] sending album ({len(photo_parts)} photos)", flush=True)
        _send_album(photo_parts)

    # Листи — окремо після всього
    if email_parts:
        _time_main.sleep(0.8)
        print(f"[report] sending email parts ({len(email_parts)} items)", flush=True)
        _send_parts_as_one(email_parts)

    print(f"=== Report {'sent' if ok else 'FAILED'} ===")

    # ── Графіки після звіту ───────────────────────────────────────────────────
    try:
        import charts as _charts_mod
        _time_main.sleep(0.8)
        print("[charts] generating combined dashboard...", flush=True)
        _chart_bytes = _charts_mod.plot_combined_dashboard()
        if _chart_bytes:
            _req_send.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                data={"chat_id": TELEGRAM_CHAT, "caption": "📊 <b>Дашборд здоров'я</b>", "parse_mode": "HTML"},
                files={"photo": ("dashboard.png", _io_send.BytesIO(_chart_bytes), "image/png")},
                timeout=60,
            )
            print("[charts] combined dashboard sent", flush=True)
            _time_main.sleep(0.5)
        # Додатково: графік дня (трекінг за сьогодні)
        _day_chart = _charts_mod.plot_day_dashboard()
        if _day_chart:
            _req_send.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                data={"chat_id": TELEGRAM_CHAT, "caption": "📈 <b>Трекінг за сьогодні</b>", "parse_mode": "HTML"},
                files={"photo": ("day_chart.png", _io_send.BytesIO(_day_chart), "image/png")},
                timeout=60,
            )
            print("[charts] day dashboard sent", flush=True)
    except Exception as _e_charts:
        print(f"[charts] error: {_e_charts}", flush=True)

    # ── Астро — надсилаємо окремим повідомленням після звіту ─────────────────
    # Астро вже є в parts (блок 6) — окреме надсилання прибрано щоб не дублювати

    # ── Кнопка "Додати в календар" після підсумку ────────────────────────────
    try:
        from planner import _tg as _planner_tg, set_state as _planner_set_state
        _now_btn = datetime.now(timezone.utc) + timedelta(hours=2)
        _planner_tg("sendMessage", {
            "chat_id": TELEGRAM_CHAT,
            "text": (
                "📅 <b>Додати в календар?</b>\n"
                "<i>Запиши зустріч, нагадування або задачу — я додам автоматично</i>"
            ),
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "✏️ Написати нагадування", "callback_data": "planner_write_today"},
                     {"text": "🛒 Що купити",            "callback_data": "shopping_add_item"}],
                    [{"text": "👍 Нічого",               "callback_data": "planner_skip"}]
                ]
            }
        })
    except Exception as _e_btn:
        print(f"planner button error: {_e_btn}")


# ─── 4c. НАГАДУВАННЯ ПРО ПОДІЇ КАЛЕНДАРЯ (за 30 хв) ──────────────────────────

CALENDAR_REMINDED_FILE = os.path.join(_DATA_DIR, "monitor_calendar_reminded.json")

def check_calendar_reminders():
    """Шле нагадування за 1 годину до старту кожної події в Google Calendar."""
    token = _calendar_access_token()
    if not token:
        return

    reminded = set(load_json_file(CALENDAR_REMINDED_FILE, default=[]))

    try:
        headers = {"Authorization": f"Bearer {token}"}
        cal_id  = "novosadovoleg%40gmail.com"

        now = datetime.now(timezone.utc)
        window_start = now + timedelta(minutes=58)
        window_end   = now + timedelta(minutes=62)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(window_start.isoformat())}"
            f"&timeMax={urllib.parse.quote(window_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=10"
        )
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        new_reminded = list(reminded)
        sent_this_run = set()  # дедуплікація по summary+start в межах одного запуску
        for ev in events:
            ev_id   = ev.get("id", "")
            summary = ev.get("summary", "(без назви)")
            start   = ev["start"].get("dateTime") or ev["start"].get("date")
            reminder_key = f"1h_{ev_id}_{start}"
            content_key  = f"1h_content_{summary}_{start}"  # захист від дублів по змісту

            if reminder_key in reminded:
                continue
            if content_key in sent_this_run:
                print(f"Duplicate content skipped: {summary} at {start}")
                new_reminded.append(reminder_key)
                continue
            sent_this_run.add(content_key)

            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                local_dt = dt.astimezone(timezone(timedelta(hours=2)))
                t = local_dt.strftime("%H:%M")
            except Exception:
                t = start
                dt = None

            # Пропускаємо якщо подія вже минула (захист від повторів після redeploy)
            if dt is not None and dt < datetime.now(timezone.utc):
                print(f"Skipping past event: {summary} at {t}")
                new_reminded.append(reminder_key)
                continue

            s_lower = summary.lower()
            if "нічна" in s_lower:
                emoji = "🌙"
                ev_tip = "Поїж перед виходом  ·  Armolopid  ·  Термос"
                ev_style = "shift_night"
            elif "рання" in s_lower:
                emoji = "☀️"
                ev_tip = "Приготуй одяг  ·  Сніданок  ·  Armolopid"
                ev_style = "shift_early"
            elif "birthday" in s_lower or "народження" in s_lower:
                emoji = "🎂"
                ev_tip = "Не забудь привітати!"
                ev_style = "birthday"
            elif "зустріч" in s_lower or "meet" in s_lower:
                emoji = "🤝"
                ev_tip = "Підготуйся до зустрічі"
                ev_style = "meeting"
            elif "лікар" in s_lower or "лікарня" in s_lower or "doctor" in s_lower:
                emoji = "🏥"
                ev_tip = "Візьми документи  ·  Запиши питання"
                ev_style = "medical"
            elif "тренуван" in s_lower or "gym" in s_lower or "спорт" in s_lower:
                emoji = "🏃"
                ev_tip = "Підготуй спорядження  ·  Вода"
                ev_style = "sport"
            else:
                emoji = "📅"
                ev_tip = ""
                ev_style = "default"

            # Різні стилі для різних типів подій
            if ev_style in ("shift_early", "shift_night"):
                msg = (
                    f"{emoji} <b>ЧЕРЕЗ 1 ГОДИНУ</b>\n"
                    f"{'═' * 22}\n"
                    f"  <b>{esc(summary)}</b>\n"
                    f"  🕐 Старт о <b>{t}</b>\n"
                    f"{'─' * 22}\n"
                    f"<i>{ev_tip}</i>"
                )
            elif ev_style == "birthday":
                msg = (
                    f"🎂 <b>ЧАС ПРИВІТАТИ!</b>\n"
                    f"<b>{esc(summary)}</b>  о {t}\n"
                    f"<i>{ev_tip}</i>"
                )
            elif ev_style == "meeting":
                msg = (
                    f"🤝 <b>Зустріч через 1 годину</b>\n"
                    f"<b>{esc(summary)}</b>\n"
                    f"🕐 о {t}  ·  <i>{ev_tip}</i>"
                )
            else:
                msg = (
                    f"{emoji} <b>Нагадування — через 1г</b>\n"
                    f"┌─ <b>{esc(summary)}</b>\n"
                    f"└─ 🕐 о <b>{t}</b>"
                    + (f"\n<i>{ev_tip}</i>" if ev_tip else "")
                )
            send_telegram(msg)
            print(f"1h reminder sent: {summary} at {t}")
            new_reminded.append(reminder_key)

        save_json_file(CALENDAR_REMINDED_FILE, new_reminded[-500:])

    except Exception as e:
        print(f"check_calendar_reminders error: {e}")


if __name__ == "__main__":
    main()


# ─── НАГАДУВАННЯ ЗА 2Г ДО ЗМІНИ ─────────────────────────────────────────────

SHIFT_REMINDED_FILE = os.path.join(_DATA_DIR, "monitor_shift_reminded.json")

def check_shift_reminders():
    """Шле нагадування за 2 години до будь-якої події в Google Calendar."""
    token = _calendar_access_token()
    if not token:
        return

    reminded = set(load_json_file(SHIFT_REMINDED_FILE, default=[]))

    try:
        token = _calendar_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        cal_id  = "novosadovoleg%40gmail.com"

        now = datetime.now(timezone.utc)
        window_start = now + timedelta(hours=1, minutes=55)
        window_end   = now + timedelta(hours=2, minutes=5)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(window_start.isoformat())}"
            f"&timeMax={urllib.parse.quote(window_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=20"
        )
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        new_reminded = list(reminded)
        sent_this_run_2h = set()  # дедуплікація по summary+start
        for ev in events:
            summary = ev.get("summary", "(без назви)")
            ev_id = ev.get("id", "")
            start = ev["start"].get("dateTime") or ev["start"].get("date")
            key          = f"2h_{ev_id}_{start}"
            content_key  = f"2h_content_{summary}_{start}"
            if key in reminded:
                continue
            if content_key in sent_this_run_2h:
                print(f"2h duplicate content skipped: {summary} at {start}")
                new_reminded.append(key)
                continue
            sent_this_run_2h.add(content_key)

            # визначаємо час
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                local_dt = dt.astimezone(timezone(timedelta(hours=2)))
                t = local_dt.strftime("%H:%M")
            except Exception:
                t = start

            # емодзі залежно від типу події
            s_lower = summary.lower()
            if "нічна" in s_lower:
                emoji = "🌙"
            elif "рання" in s_lower or "ранн" in s_lower:
                emoji = "☀️"
            elif "день народження" in s_lower or "birthday" in s_lower:
                emoji = "🎂"
            elif "зустріч" in s_lower or "meet" in s_lower:
                emoji = "🤝"
            else:
                emoji = "📅"

            msg = (
                f"{emoji} <b>Нагадування — через 2 години:</b>\n"
                f"<b>{esc(summary)}</b>\n"
                f"🕐 Початок о <b>{t}</b>"
            )
            send_telegram(msg)
            print(f"2h reminder sent: {summary} at {t}")
            new_reminded.append(key)

        save_json_file(SHIFT_REMINDED_FILE, new_reminded[-500:])

    except Exception as e:
        print(f"check_shift_reminders error: {e}")


# ─── РАНКОВИЙ БРИФІНГ (7:00 у вихідні) ───────────────────────────────────────

MORNING_BRIEF_FILE = os.path.join(_DATA_DIR, "monitor_morning_brief.json")

def check_morning_brief():
    """
    🌅 MEGA РАНКОВИЙ БРИФІНГ — о 07:00 щодня (адаптується до типу дня).
    Містить: привітання + тип дня, погода, крипто dashboard,
             статус звичок вчора, вага (графік 7 днів), AI порада.
    """
    import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")
    yesterday = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")

    state = load_json_file(MORNING_BRIEF_FILE, default={})
    if state.get("last") == today:
        return

    try:
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        shift = shift_info.get("today", "free")
        tomorrow_shift = shift_info.get("tomorrow", "free")
    except Exception:
        shift = "free"
        tomorrow_shift = "free"

    # Тригер: рання о 05:00, нічна/вихідний о 07:00, після нічної о 11:00
    if shift == "early":
        trigger_h = 5
    elif shift == "after_night":
        trigger_h = 11
    else:
        trigger_h = 7

    if not (h == trigger_h and 0 <= m < 3):
        return

    DAY_UA = ["Понеділок","Вівторок","Середа","Четвер","П'ятниця","Субота","Неділя"]
    day_name = DAY_UA[now_local.weekday()]

    # ── Заголовок ───────────────────────────────────────────────────────────
    if shift == "early":
        header = f"☀️ <b>Доброго ранку, {day_name}!</b>\n💼 Рання зміна — виходити о 05:30 → Вихід вже скоро!"
        mood = "⚡️ Енергійного робочого дня!"
    elif shift == "night":
        header = f"🌙 <b>Доброго ранку, {day_name}!</b>\n🔴 Нічна зміна — виходити о 17:30 → є час відпочити"
        mood = "😴 Відпочинь перед ніччю — збережи сили!"
    elif shift == "after_night":
        header = f"😴 <b>Після нічної, {day_name}.</b>\n🛋 Вчора була нічна зміна — сьогодні режим відновлення."
        mood = "💤 Відпочивай, не планируй забагато на сьогодні."
    else:
        header = f"🌅 <b>Доброго ранку, {day_name}!</b>\n🏖 Вихідний — твій день, використай добре!"
        mood = "💪 Продуктивного та приємного дня!"

    lines_out = [header, ""]

    # ── Погода ──────────────────────────────────────────────────────────────
    try:
        WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY","")
        CITY = "Kosice"
        if WEATHER_API_KEY:
            url_w = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={WEATHER_API_KEY}&units=metric&lang=uk"
            req_w = urllib.request.Request(url_w, headers={"User-Agent":"bot"})
            with urllib.request.urlopen(req_w, timeout=8) as r:
                wd = json.loads(r.read())
            temp = round(wd["main"]["temp"])
            feels = round(wd["main"]["feels_like"])
            desc = wd["weather"][0]["description"]
            wind = wd["wind"]["speed"]
            humidity = wd["main"]["humidity"]
            # Емодзі за описом
            if any(x in desc for x in ["дощ","злива"]): w_icon = "🌧"
            elif "гроза" in desc: w_icon = "⛈"
            elif any(x in desc for x in ["сніг","хурто"]): w_icon = "❄️"
            elif "хмар" in desc: w_icon = "☁️"
            elif "ясно" in desc or "сонячно" in desc: w_icon = "☀️"
            else: w_icon = "🌤"
            # Температурний рейтинг
            if temp >= 20: t_mood = "🔥 тепло"
            elif temp >= 10: t_mood = "😊 комфортно"
            elif temp >= 0: t_mood = "🧥 прохолодно"
            else: t_mood = "🥶 мороз"
            lines_out.append(f"{w_icon} <b>Погода</b> · {temp}°C ({t_mood}) · {desc}")
            lines_out.append(f"   💨 {wind} м/с · 💧 {humidity}% · відчувається {feels}°C")
            lines_out.append("")
    except Exception:
        pass

    # ── Крипто dashboard ────────────────────────────────────────────────────
    try:
        sym_map = list(COINS.items())
        ids = ",".join(cg_id for _, cg_id in sym_map)
        url_c = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}&price_change_percentage=24h,7d,30d"
        req_c = urllib.request.Request(url_c, headers={"User-Agent":"bot"})
        with urllib.request.urlopen(req_c, timeout=8) as r:
            raw_c = json.loads(r.read())
        data_c = {c["id"]: c for c in raw_c}

        def _trend_emoji(pct):
            """Емодзі тренду замість бару."""
            if pct is None: return "➡️"
            if pct > 5:  return "🚀"
            if pct > 2:  return "📈"
            if pct > 0:  return "🟢"
            if pct > -2: return "🔴"
            if pct > -5: return "📉"
            return "💥"

        crypto_lines = []
        for sym, cid in sym_map:
            c = data_c.get(cid, {})
            price = c.get("current_price")
            ch24  = c.get("price_change_percentage_24h") or 0
            ch7   = c.get("price_change_percentage_7d_in_currency") or 0
            ch30  = c.get("price_change_percentage_30d_in_currency") or 0
            if price is None: continue
            sign24 = "+" if ch24 >= 0 else ""
            sign7  = "+" if ch7  >= 0 else ""
            sign30 = "+" if ch30 >= 0 else ""
            e24  = _trend_emoji(ch24)
            e7   = _trend_emoji(ch7)
            e30  = _trend_emoji(ch30)
            price_fmt = f"${price:,.0f}" if price >= 1 else f"${price:.4f}"
            crypto_lines.append(
                f"{e24} <b>{sym}</b> {price_fmt}\n"
                f"   День: {sign24}{ch24:.1f}% {e24}  Тиждень: {sign7}{ch7:.1f}% {e7}  Місяць: {sign30}{ch30:.1f}% {e30}"
            )

        if crypto_lines:
            lines_out.append("💹 <b>Крипто</b>")
            lines_out.extend(crypto_lines)
            lines_out.append("")
    except Exception:
        pass

    # ── Звички вчора ────────────────────────────────────────────────────────
    try:
        from storage import load_habits as _lh
        habits_db = _lh()
        yest_habits = habits_db.get(yesterday, {})
        if yest_habits:
            HABIT_MAP = [("run","🏃","Біг"),("water","💧","Вода"),("shower","🚿","Душ"),("tea","🍵","Чай")]
            habit_parts = []
            for hid, hico, hname in HABIT_MAP:
                v = yest_habits.get(hid)
                mark = "✅" if v is True else ("❌" if v is False else "⬜")
                habit_parts.append(f"{hico}{mark}")
            lines_out.append(f"📊 <b>Вчора</b>  {'  '.join(habit_parts)}")
            lines_out.append("")
    except Exception:
        pass

    # ── Графік ваги (7 днів ASCII) ──────────────────────────────────────────
    try:
        from storage import load_weight as _lw
        wdata = _lw()
        if wdata:
            w_days = sorted(wdata.keys())[-7:]
            w_vals = [wdata[d] for d in w_days if wdata.get(d)]
            if len(w_vals) >= 2:
                w_min = min(w_vals) - 0.5
                w_max = max(w_vals) + 0.5
                w_range = w_max - w_min or 1
                bars = []
                for v in w_vals:
                    bar_h = int((v - w_min) / w_range * 5)
                    bar_h = max(1, min(5, bar_h))
                    blocks = ["⬜","🟦","🟦","🟩","🟩","🟨","🟧","🟥"]
                    bars.append(blocks[bar_h])
                trend = "↗️" if w_vals[-1] > w_vals[0] else ("↘️" if w_vals[-1] < w_vals[0] else "→")
                last_w = w_vals[-1]
                diff_goal = round(last_w - 78.0, 1)
                goal_str = f"до цілі: -{diff_goal} кг" if diff_goal > 0 else "✅ ЦІЛЬ ДОСЯГНУТА!"
                lines_out.append(f"⚖️ <b>Вага</b>  {last_w} кг  {trend}  ({goal_str})")
                lines_out.append(f"   <code>{''.join(bars)}</code>  7 днів")
                lines_out.append("")
    except Exception:
        pass

    # ── AI порада на день (з урахуванням КАЛЕНДАРЯ) ─────────────────────────
    gemini_key = os.environ.get("GEMINI_API_KEY","")
    if gemini_key:
        try:
            import uuid as _uuid_brief
            shift_labels = {"early":"рання зміна 06:00–18:00","night":"нічна зміна 17:00–05:00","free":"вихідний"}
            # Отримуємо події календаря
            cal_events_for_ai = _get_calendar_events_text()
            cal_ctx_brief = (
                f"Заплановано сьогодні: {cal_events_for_ai}"
                if cal_events_for_ai and cal_events_for_ai != "нічого не заплановано"
                else "Подій у календарі немає"
            )
            brief_seed = str(_uuid_brief.uuid4())[:8]
            prompt = (
                f"Ти асистент Олега (Кошіце). Сьогодні {day_name} {now_local.strftime('%d.%m.%Y')}. [id:{brief_seed}]\n"
                f"Тип дня: {shift_labels.get(shift,'вихідний')}.\n"
                f"{cal_ctx_brief}\n\n"
                f"Дай ОДНУ конкретну actionable пораду на ЦЕЙ КОНКРЕТНИЙ день. "
                f"Якщо є події в календарі — враховуй їх. "
                f"Ціль: схуднення до 78 кг, регулярний біг, крипто-інвестиції. "
                f"1-2 речення, бадьоро, тільки конкретика. Українська."
            )
            payload = json.dumps({"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":600,"temperature":0.95}}).encode()
            req_ai = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                data=payload, headers={"Content-Type":"application/json"}, method="POST"
            )
            with urllib.request.urlopen(req_ai, timeout=15) as r:
                resp = json.loads(r.read())
            ai_tip = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
            lines_out.append(f"🤖 <i>{ai_tip}</i>")
            lines_out.append("")
        except Exception as e:
            print(f"morning brief AI error: {e}")

    lines_out.append(f"<i>{mood}</i>")

    # Зберігаємо ПЕРЕД відправкою — захист від дублів при Railway restart
    state["last"] = today
    save_json_file(MORNING_BRIEF_FILE, state)
    msg_out = "\n".join(lines_out)
    send_telegram(msg_out)
    print(f"Morning brief sent: {today} shift={shift}")



PROACTIVE_FILE = os.path.join(_DATA_DIR, "monitor_proactive.json")


def _get_calendar_events_text() -> str:
    """Повертає короткий список подій з Google Calendar на СЬОГОДНІ (для AI промптів)."""
    try:
        token = _calendar_access_token()
        if not token:
            return ""
        now_utc = datetime.now(timezone.utc)
        local_start = (now_utc + timedelta(hours=2)).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        local_end   = local_start + timedelta(hours=24)
        cal_id = "novosadovoleg%40gmail.com"
        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(local_start.isoformat())}"
            f"&timeMax={urllib.parse.quote(local_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=15"
        )
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            items = json.loads(r.read()).get("items", [])
        if not items:
            return "нічого не заплановано"
        lines = []
        for ev in items:
            start = ev["start"].get("dateTime") or ev["start"].get("date")
            summary = ev.get("summary", "(без назви)")
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                t  = dt.astimezone(timezone(timedelta(hours=2))).strftime("%H:%M")
            except Exception:
                t = ""
            lines.append(f"{t} {summary}".strip())
        return "; ".join(lines)
    except Exception as e:
        print(f"_get_calendar_events_text error: {e}")
        return ""


def _ai_personal_message(situation: str, context: dict = None, max_tokens: int = 200) -> str:
    """
    Генерує реальне персоналізоване повідомлення через Gemini.
    situation — опис ситуації (що відбувається).
    context — словник з додатковими даними (вага, сон, кроки, зміна, тощо).

    Включає ЗАВЖДИ:
    - поточний стан календаря (що заплановано сьогодні)
    - унікальний seed (UUID) щоб Gemini не повторювався
    - реальні дані (вага, звички, здоров'я, крипто)
    """
    import uuid as _uuid
    gemini_key = os.environ.get("GEMINI_API_KEY", "AIzaSyDRXcGERTNILIEDKbmgTKSXUuiwt1oKeGM")

    # Збираємо реальний контекст
    ctx_parts = []

    # Вага
    try:
        from storage import load_weight as _lw
        wd = _lw()
        if wd:
            last_key = sorted(wd.keys())[-1]
            ctx_parts.append(f"Вага: {wd[last_key]} кг (ціль 78 кг, залишилось {wd[last_key]-78:.1f} кг)")
    except: pass

    # Здоров'я
    try:
        from storage import load_health as _lhh
        hd = _lhh()
        if hd:
            last_k = sorted(hd.keys())[-1]
            h = hd[last_k]
            parts_h = []
            if h.get("steps"): parts_h.append(f"кроки {h['steps']}")
            if h.get("sleep_hours"): parts_h.append(f"сон {h['sleep_hours']}г")
            if h.get("hrv"): parts_h.append(f"HRV {h['hrv']}")
            if parts_h: ctx_parts.append(f"Вчора ({last_k}): {', '.join(parts_h)}")
    except: pass

    # Звички за тиждень
    try:
        from habits import load_data as _lhab
        hab_data = _lhab()
        now_l = datetime.now(timezone.utc) + timedelta(hours=2)
        days7 = [(now_l - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6,-1,-1)]
        run_count = sum(1 for d in days7 if hab_data.get(d,{}).get("run") is True)
        ctx_parts.append(f"Пробіжки за 7 днів: {run_count}/7")
    except: pass

    # Крипто (швидко)
    try:
        ids = "bitcoin,ethereum"
        url_c = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
        req_c = urllib.request.Request(url_c, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req_c, timeout=6) as r:
            cd = json.loads(r.read())
        btc_ch = cd.get("bitcoin", {}).get("usd_24h_change", 0)
        eth_ch = cd.get("ethereum", {}).get("usd_24h_change", 0)
        ctx_parts.append(f"Крипто зараз: BTC {btc_ch:+.1f}%, ETH {eth_ch:+.1f}% за 24г")
    except: pass

    # КАЛЕНДАР — ключова нова частина
    cal_events = _get_calendar_events_text()
    if cal_events:
        ctx_parts.append(f"Календар сьогодні: {cal_events}")

    # Додаткові дані з параметра
    if context:
        for k, v in context.items():
            ctx_parts.append(f"{k}: {v}")

    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    weekday_ua = ["понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя"][now_local.weekday()]
    # Унікальний seed — гарантує що Gemini не дає однаковий текст
    msg_seed = str(_uuid.uuid4())[:8]

    profile = (
        "Ти — персональний асистент Олега Новосадова (живе в Кошіце, Словаччина). "
        "Олег: завод Minebea Mitsumi, змінна робота (рання 06-18 / нічна 18-06 / вихідний), "
        "цілі — схуднути до 78 кг, регулярно бігати, інвестиції в крипто (BTC,ETH,AVAX,ONDO), "
        "приймає ліки Armolopid щодня (курс 27.04–27.07.2026). "
        "Стиль: як близький друг — по-українськи, без шаблонних фраз. "
        "Враховуй ПОДІЇ КАЛЕНДАРЯ — якщо є заплановане, пов'яжи пораду з цим. "
        "Якщо нічого не заплановано — підкажи що зробити виходячи з цілей. "
    )

    prompt = (
        f"{profile}\n\n"
        f"Сьогодні {weekday_ua} {now_local.strftime('%d.%m.%Y')}, {now_local.strftime('%H:%M')}. [seed:{msg_seed}]\n"
        f"Реальні дані:\n" + "\n".join(f"• {p}" for p in ctx_parts) + "\n\n"
        f"Ситуація: {situation}\n\n"
        f"Напиши НОВЕ унікальне повідомлення (2-4 речення) з конкретними порадами "
        f"на основі РЕАЛЬНИХ даних вище. ОБОВ'ЯЗКОВО врахуй календар. "
        f"Без шаблонних фраз. Тільки конкретика і нова інформація."
    )

    try:
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max(max_tokens, 600),
                "temperature": 0.95
            },
        }).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
            data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            resp = json.loads(r.read())
        text = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Якщо текст обривається — спробуємо ще раз з більшим ліком
        if text and not text[-1] in ".!?»":
            text += "."
        return text
    except Exception as e:
        print(f"_ai_personal_message error: {e}")
        return ""


def check_proactive_insights():
    """
    Ініціативні повідомлення на основі профілю Олега:
    - Перед/після змін на роботі
    - Мотивація у вільні дні
    - Тижневий підсумок (бігу, ваги)
    - Крипто тренди
    - Нагадування про цілі
    """
    now_utc  = datetime.now(timezone.utc)
    now_local = now_utc + timedelta(hours=2)  # CEST UTC+2 (Кошіце)
    h, m  = now_local.hour, now_local.minute
    dow   = now_local.weekday()  # 0=пн, 6=нд
    today = now_local.strftime("%Y-%m-%d")

    if not (0 <= m < 5):  # тільки на початку кожної години
        return

    state = load_json_file(PROACTIVE_FILE, default={})

    def already_sent(key):
        return state.get(key) == today

    def mark_sent(key):
        state[key] = today
        save_json_file(PROACTIVE_FILE, state)

    # ── Отримуємо календар на сьогодні і завтра ───────────────────────────────
    today_events = []
    tomorrow_events = []
    try:
        token = _calendar_access_token()
        if token:
            headers = {"Authorization": f"Bearer {token}"}
            cal_id = "novosadovoleg%40gmail.com"

            for offset, store in [(0, "today_events"), (1, "tomorrow_events")]:
                day = now_local + timedelta(days=offset)
                tmin = day.replace(hour=0, minute=0, second=0, microsecond=0)
                tmax = tmin + timedelta(hours=24)
                url = (
                    f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
                    f"?timeMin={urllib.parse.quote(tmin.isoformat()+'Z'.replace('+01:00Z','Z'))}"
                    f"&timeMax={urllib.parse.quote(tmax.isoformat()+'Z'.replace('+01:00Z','Z'))}"
                    f"&singleEvents=true&orderBy=startTime&maxResults=20"
                )
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as r:
                    evs = json.loads(r.read()).get("items", [])
                if offset == 0:
                    today_events = evs
                else:
                    tomorrow_events = evs
    except Exception as e:
        print(f"proactive calendar error: {e}")

    def get_shift(events):
        """Повертає ('early'/'night'/None, start_dt)"""
        for ev in events:
            s = ev.get("summary", "").lower()
            if "рання" in s:
                start = ev["start"].get("dateTime","")
                try:
                    dt = datetime.fromisoformat(start.replace("Z","+00:00")) + timedelta(hours=2)
                    return ("early", dt)
                except: return ("early", None)
            if "нічна" in s:
                start = ev["start"].get("dateTime","")
                try:
                    dt = datetime.fromisoformat(start.replace("Z","+00:00")) + timedelta(hours=2)
                    return ("night", dt)
                except: return ("night", None)
        return (None, None)

    today_shift, today_shift_dt   = get_shift(today_events)
    tomorrow_shift, tomorrow_shift_dt = get_shift(tomorrow_events)

    # ── 1. Ранкове привітання з AI (08:00, вільний день) ─────────────────────
    # ── 1. Ранкове привітання — ВИМКНЕНО тут, обробляється в check_morning_context (08:00 вільний)
    #    щоб уникнути дублювання ранкових повідомлень
    if False and h == 8 and not today_shift and not already_sent("morning_free"):
        pass  # логіка перенесена в check_morning_context

    # ── 2 & 3. Нагадування перед зміною — перенесено в check_smart_notifications
    #    (04:30 рання, 16:30 нічна) щоб уникнути дублювання

    # ── 4. Після нічної зміни — перенесено в check_smart_notifications (06:15)
    #    щоб уникнути дублювання

    # ── 5. Тижневий підсумок ваги (неділя 20:00) з AI аналізом ──────────────
    if dow == 6 and h == 20 and not already_sent("weekly_weight"):
        try:
            import storage as _ws; weight_data = _ws.load("weight_data.json") or _ws.load_weight() or {}
            if weight_data:
                sorted_w = sorted(weight_data.items())
                last_entries = sorted_w[-7:]
                if last_entries:
                    last_date, last_w = last_entries[-1]
                    recent_change = last_w - last_entries[0][1] if len(last_entries) > 1 else 0
                    to_goal = last_w - 78.0
                    trend = "📉" if recent_change < -0.2 else "📈" if recent_change > 0.2 else "➡️"

                    # AI аналіз тренду
                    weight_history = ", ".join(f"{d}:{w}кг" for d, w in last_entries[-5:])
                    ai_msg = _ai_personal_message(
                        f"Тижневий підсумок ваги. Остання: {last_w} кг, зміна за тиждень: {recent_change:+.1f} кг, до цілі: {to_goal:.1f} кг.",
                        context={"Динаміка": weight_history}
                    )
                    msg = (
                        f"⚖️ <b>Тижневий підсумок ваги</b>\n\n"
                        f"Зараз: <b>{last_w} кг</b> ({last_date})\n"
                        f"{trend} За тиждень: {recent_change:+.1f} кг\n"
                        f"До цілі 78 кг: <b>{to_goal:.1f} кг</b>\n"
                    )
                    if ai_msg:
                        msg += f"\n{ai_msg}"
                    mark_sent("weekly_weight")
                    send_telegram(msg)
        except Exception as e:
            print(f"weekly weight error: {e}")

    # ── 6. Нагадування про пробіжку (вт/чт/сб о 09:00 вільний) з реальною погодою
    if h == 9 and dow in (1, 3, 5) and not today_shift and not already_sent("run_motivation"):
        weather_str = ""
        try:
            weather_str = get_weather().split("\n")[0]
        except: pass
        ai_msg = _ai_personal_message(
            "Добрий ранок вільного дня — час для пробіжки (09:00).",
            context={"Погода зараз": weather_str or "невідома"}
        )
        msg = f"🏃 <b>Час для пробіжки!</b>"
        if weather_str:
            msg += f"\n🌤 {weather_str}"
        if ai_msg:
            msg += f"\n\n{ai_msg}"
        mark_sent("run_motivation")
        send_telegram(msg)

    # ── 7. Понеділок — реальний огляд тижня (пн 09:00, вільний) ─────────────
    if h == 9 and dow == 0 and not today_shift and not already_sent("monday_goals"):
        # Реальний тижневий контекст
        cal_next = ""
        try:
            _week_token = _calendar_access_token()
            if _week_token:
                token = _week_token
                tmin = now_local.replace(hour=0,minute=0,second=0,microsecond=0)
                tmax = tmin + timedelta(days=7)
                url = (
                    f"https://www.googleapis.com/calendar/v3/calendars/novosadovoleg%40gmail.com/events"
                    f"?timeMin={urllib.parse.quote(tmin.isoformat())}"
                    f"&timeMax={urllib.parse.quote(tmax.isoformat())}"
                    f"&singleEvents=true&orderBy=startTime&maxResults=15"
                )
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    week_events = json.loads(r.read()).get("items", [])
                shifts = [e.get("summary","") for e in week_events if "зміна" in e.get("summary","").lower()]
                other = [e.get("summary","") for e in week_events if "зміна" not in e.get("summary","").lower()]
                cal_next = f"{len(shifts)} змін, інші події: {', '.join(other[:3])}" if other else f"{len(shifts)} змін"
        except: pass

        ai_msg = _ai_personal_message(
            "Понеділок — початок тижня. Огляд цілей і планування.",
            context={"Календар тижня": cal_next or "не завантажено"}
        )
        msg = f"🎯 <b>Понеділок — план тижня</b>"
        if cal_next:
            msg += f"\n📅 {cal_next}"
        if ai_msg:
            msg += f"\n\n{ai_msg}"
        mark_sent("monday_goals")
        send_telegram(msg)


# ─── КРИПТО АЛЕРТ >5% ЗА ГОДИНУ ──────────────────────────────────────────────

CRYPTO_ALERT_FILE = os.path.join(_DATA_DIR, "monitor_crypto_alert.json")

def check_crypto_price_alert():
    """Шле сповіщення якщо BTC/ETH/AVAX/ONDO змінились >5% за ~1 годину.
    Логіка: зберігаємо snapshot цін в GitHub storage кожні 15хв,
    порівнюємо з snapshot що був ~60хв тому.
    """
    now_ts  = int(time.time())
    now_str = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d %H")

    # ── Завантажуємо поточні ціни ────────────────────────────────────────────
    ids  = ",".join(COINS.values())
    url  = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
    data = fetch_json(url)
    if not data:
        # Fallback на Kraken
        data = _get_prices_kraken()
    if not data:
        return

    # ── Поточні ціни ─────────────────────────────────────────────────────────
    current = {}
    for symbol, cg_id in COINS.items():
        price = data.get(cg_id, {}).get("usd") if isinstance(data.get(cg_id), dict) else None
        if price:
            current[cg_id] = price

    if not current:
        return

    # ── Читаємо/оновлюємо snapshot з GitHub storage ───────────────────────────
    snapshots = storage.load("crypto_price_snapshots.json", default={})
    # snapshots = {cg_id: [[ts, price], ...]}  — хронологічний список

    alerts = []
    alert_key_prefix = f"alerted_{now_str}"

    for symbol, cg_id in COINS.items():
        # Алерти ТІЛЬКИ для монет Олега: BTC/ETH/AVAX/ONDO
        if symbol not in ALERT_COINS:
            # Все одно зберігаємо snapshot для майбутнього використання
            price = current.get(cg_id)
            if price:
                pts = snapshots.get(cg_id, [])
                if not pts or (now_ts - pts[-1][0]) >= 600:
                    pts.append([now_ts, price])
                pts = [[ts, p] for ts, p in pts if (now_ts - ts) <= 7200]
                snapshots[cg_id] = pts
            continue

        price = current.get(cg_id)
        if not price:
            continue

        pts = snapshots.get(cg_id, [])

        # Знаходимо точку ~45-75 хв тому
        ref_price = None
        for ts_old, p_old in reversed(pts):
            age_min = (now_ts - ts_old) / 60
            if 45 <= age_min <= 75:
                ref_price = p_old
                break

        # Якщо немає ~1год точки — беремо найстарішу з доступних (>30хв)
        if ref_price is None:
            for ts_old, p_old in pts:
                if (now_ts - ts_old) >= 1800:
                    ref_price = p_old
                    break

        if ref_price and ref_price > 0:
            pct = (price - ref_price) / ref_price * 100
            alert_key = f"{cg_id}_{now_str}"
            already_sent = snapshots.get("_alerts_sent", {}).get(alert_key)

            if abs(pct) >= 5 and not already_sent:
                arrow = "🚀" if pct > 0 else "💥"
                sign  = "+" if pct > 0 else ""
                age_h = (now_ts - [ts for ts, _ in pts if _ == ref_price][0] if [(ts, p) for ts, p in pts if p == ref_price] else now_ts - now_ts + 3600) / 3600
                alerts.append(
                    f"{arrow} <b>{symbol}</b> {sign}{pct:.1f}% за ~1г\n"
                    f"   Зараз: <code>${price:,.2f}</code>  Було: <code>${ref_price:,.2f}</code>"
                )
                if "_alerts_sent" not in snapshots:
                    snapshots["_alerts_sent"] = {}
                snapshots["_alerts_sent"][alert_key] = True

        # Додаємо поточну точку (не частіше ніж раз на 10хв)
        if not pts or (now_ts - pts[-1][0]) >= 600:
            pts.append([now_ts, price])
        # Тримаємо тільки останні 2 години
        pts = [[ts, p] for ts, p in pts if (now_ts - ts) <= 7200]
        snapshots[cg_id] = pts

    # Чистимо старі ключі алертів (старші 6г)
    if "_alerts_sent" in snapshots:
        cutoff = (datetime.now(timezone.utc) + timedelta(hours=2) - timedelta(hours=6)).strftime("%Y-%m-%d %H")
        snapshots["_alerts_sent"] = {
            k: v for k, v in snapshots["_alerts_sent"].items()
            if k.split("_")[-1] >= cutoff
        }

    storage.save("crypto_price_snapshots.json", snapshots)

    if alerts:
        msg = "⚡ <b>Крипто алерт!</b>\n\n" + "\n\n".join(alerts)
        send_telegram(msg)
        print(f"Crypto price alert sent: {len(alerts)} coins")


# ─── ETF PRICE ALERT ──────────────────────────────────────────────────────────

ETF_ALERT_TICKERS = [
    ("IBIT",  "IBIT"),
    ("ETHA",  "ETHA"),
    ("VAVA",  "VAVA.SW"),
    ("GAVA",  "GAVA"),
    ("QQQ",   "QQQ"),
    ("SPY",   "SPY"),
    ("S&P500","^GSPC"),
    ("NVDA",  "NVDA"),
    ("AAPL",  "AAPL"),
    ("TSLA",  "TSLA"),
    ("COIN",  "COIN"),
]
ETF_ALERT_THRESHOLD = 3.0  # % зміна за ~1г для алерту

def check_etf_price_alert():
    """Шле сповіщення якщо ETF/S&P500 змінились >3% за ~1 годину.
    Працює тільки в торгові години NYSE (14:30–21:00 UTC).
    """
    try:
        import yfinance as yf
    except ImportError:
        return

    now_utc = datetime.now(timezone.utc)
    now_ts  = int(time.time())
    now_str = (now_utc + timedelta(hours=2)).strftime("%Y-%m-%d %H")

    # NYSE торгується 14:30–21:00 UTC, пн-пт
    weekday = now_utc.weekday()  # 0=пн, 6=нд
    hour_utc = now_utc.hour + now_utc.minute / 60
    if weekday >= 5 or not (14.5 <= hour_utc <= 21.0):
        return  # ринок закритий

    # ── Поточні ціни ─────────────────────────────────────────────────────────
    current = {}
    for name, sym in ETF_ALERT_TICKERS:
        try:
            h = yf.Ticker(sym).history(period="1d", interval="5m")
            if len(h) > 0:
                current[name] = float(h["Close"].iloc[-1])
        except Exception:
            pass

    if not current:
        return

    # ── Snapshot з GitHub storage ─────────────────────────────────────────────
    snaps = storage.load("etf_price_snapshots.json", default={})
    alerts = []

    for name, sym in ETF_ALERT_TICKERS:
        price = current.get(name)
        if not price:
            continue

        pts = snaps.get(name, [])

        # Шукаємо точку ~45-75 хв тому
        ref_price = None
        for ts_old, p_old in reversed(pts):
            age_min = (now_ts - ts_old) / 60
            if 45 <= age_min <= 90:
                ref_price = p_old
                break
        if ref_price is None:
            for ts_old, p_old in pts:
                if (now_ts - ts_old) >= 1800:
                    ref_price = p_old
                    break

        if ref_price and ref_price > 0:
            pct = (price - ref_price) / ref_price * 100
            alert_key = f"{name}_{now_str}"
            already_sent = snaps.get("_alerts_sent", {}).get(alert_key)

            if abs(pct) >= ETF_ALERT_THRESHOLD and not already_sent:
                arrow = "🚀" if pct > 0 else "💥"
                sign  = "+" if pct > 0 else ""
                alerts.append(
                    f"{arrow} <b>{name}</b> {sign}{pct:.1f}% за ~1г\n"
                    f"   Зараз: <code>${price:,.2f}</code>  Було: <code>${ref_price:,.2f}</code>"
                )
                if "_alerts_sent" not in snaps:
                    snaps["_alerts_sent"] = {}
                snaps["_alerts_sent"][alert_key] = True

        # Зберігаємо точку (не частіше ніж раз на 10хв)
        if not pts or (now_ts - pts[-1][0]) >= 600:
            pts.append([now_ts, price])
        pts = [[ts, p] for ts, p in pts if (now_ts - ts) <= 7200]
        snaps[name] = pts

    # Чистимо старі ключі алертів
    if "_alerts_sent" in snaps:
        cutoff = (now_utc + timedelta(hours=2) - timedelta(hours=6)).strftime("%Y-%m-%d %H")
        snaps["_alerts_sent"] = {
            k: v for k, v in snaps["_alerts_sent"].items()
            if k.split("_")[-1] >= cutoff
        }

    storage.save("etf_price_snapshots.json", snaps)

    if alerts:
        msg = "📊 <b>ETF алерт!</b>\n\n" + "\n\n".join(alerts)
        send_telegram(msg)
        print(f"ETF price alert sent: {len(alerts)} tickers")


# ─── СТАТИСТИКА ЗВИЧОК ЗА ТИЖДЕНЬ (щопонеділка 9:00) ─────────────────────────

HABIT_STATS_FILE = os.path.join(_DATA_DIR, "monitor_habit_stats.json")

def check_weekly_habit_stats():
    """
    📊 WEEKLY HABIT DASHBOARD — щопонеділка о 09:00.
    Красивий ASCII дашборд: стрік, відсотки, тренди, AI аналіз.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.weekday() == 0 and now_local.hour == 9 and now_local.minute < 5):
        return

    state = load_json_file(HABIT_STATS_FILE, default={})
    today = now_local.strftime("%Y-%m-%d")
    if state.get("last") == today:
        return

    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from storage import load_habits as _lh, load_weight as _lw
        data = _lh()
        if not data:
            return

        days7 = [(now_local - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        days_short = [(now_local - timedelta(days=i)).strftime("%a") for i in range(6, -1, -1)]
        days_short_ua = ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"]
        # яким день тижня була 6 днів тому
        start_dow = (now_local - timedelta(days=6)).weekday()
        day_labels = [days_short_ua[(start_dow + i) % 7] for i in range(7)]

        HABITS = [
            ("run",    "🏃", "Біг"),
            ("water",  "💧", "Вода"),
            ("shower", "🚿", "Хол.душ"),
            ("tea",    "🍵", "Чай"),
        ]

        logs = data if isinstance(data, dict) else {}

        header_row = "  " + " ".join(f"{d:>2}" for d in day_labels)
        lines_out = []
        lines_out.append(f"📊 <b>ТИЖНЕВИЙ ДАШБОРД</b>")
        lines_out.append(f"<code>{header_row}</code>")
        lines_out.append("")

        total_score = 0
        habit_scores = {}

        for hid, hico, hname in HABITS:
            row_marks = []
            count = 0
            streak = 0
            current_streak = 0
            for d in days7:
                v = logs.get(d, {}).get(hid)
                if v is True:
                    row_marks.append("✅")
                    count += 1
                    current_streak += 1
                    streak = max(streak, current_streak)
                elif v is False:
                    row_marks.append("❌")
                    current_streak = 0
                else:
                    row_marks.append("⬜")
                    current_streak = 0
            pct = int(count / 7 * 100)
            total_score += pct
            habit_scores[hid] = pct

            # Рейтинг
            if pct >= 86: grade = "🏆"
            elif pct >= 57: grade = "⭐️"
            elif pct >= 29: grade = "👍"
            else: grade = "💤"

            marks_str = " ".join(row_marks)
            lines_out.append(f"{hico} <b>{hname}</b> {count}/7 {grade}")
            lines_out.append(f"<code>  {marks_str}</code>")

        lines_out.append("")

        # Загальний рейтинг тижня
        avg_pct = total_score // len(HABITS) if HABITS else 0
        if avg_pct >= 85: week_grade = "🏆 ІДЕАЛЬНИЙ ТИЖДЕНЬ!"
        elif avg_pct >= 65: week_grade = "⭐️ Відмінний тиждень!"
        elif avg_pct >= 45: week_grade = "👍 Непоганий тиждень"
        elif avg_pct >= 25: week_grade = "😐 Середній тиждень"
        else: week_grade = "💤 Слабкий тиждень — наступний кращий!"

        # Заповненість смужки
        filled = int(avg_pct / 100 * 10)
        progress_bar = "🟩" * filled + "⬜" * (10 - filled)
        lines_out.append(f"<code>[{progress_bar}]</code> {avg_pct}%  {week_grade}")
        lines_out.append("")

        # Тренд ваги за тиждень
        try:
            wdata = _lw()
            if wdata:
                w_days_data = {d: wdata[d] for d in days7 if d in wdata}
                if len(w_days_data) >= 2:
                    sorted_keys = sorted(w_days_data.keys())
                    w_start = w_days_data[sorted_keys[0]]
                    w_end   = w_days_data[sorted_keys[-1]]
                    diff = round(w_end - w_start, 1)
                    to_goal = round(w_end - 78.0, 1)
                    trend = "↗️ +{:.1f} кг".format(diff) if diff > 0 else "↘️ {:.1f} кг".format(diff)
                    lines_out.append(f"⚖️ <b>Вага за тиждень:</b> {w_start}→{w_end} кг  {trend}")
                    if to_goal > 0:
                        lines_out.append(f"   🎯 До цілі 78 кг: ще -{to_goal} кг")
                    else:
                        lines_out.append(f"   🏆 Ціль 78 кг ДОСЯГНУТА!")
                    lines_out.append("")
        except Exception:
            pass

        # AI аналіз тижня
        gemini_key = os.environ.get("GEMINI_API_KEY","")
        if gemini_key:
            try:
                habit_summary = ", ".join([f"{hname}: {habit_scores[hid]}%" for hid,_,hname in HABITS])
                prompt = (
                    f"Аналіз тижня Олега: {habit_summary}. "
                    f"Загальний результат: {avg_pct}%. "
                    f"Дай 1-2 речення: що вийшло добре і що покращити наступного тижня. "
                    f"Конкретно, без загальних слів."
                )
                payload = json.dumps({"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":600,"temperature":0.7}}).encode()
                req = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    data=payload, headers={"Content-Type":"application/json"}, method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    resp = json.loads(r.read())
                ai_analysis = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
                lines_out.append(f"🤖 <i>{ai_analysis}</i>")
                lines_out.append("")
            except Exception as e:
                print(f"habit stats AI error: {e}")

        lines_out.append("💪 Новий тиждень — новий шанс!")

        send_telegram("\n".join(lines_out))
        state["last"] = today
        save_json_file(HABIT_STATS_FILE, state)
        print("Weekly habit stats sent")

    except Exception as e:
        print(f"check_weekly_habit_stats error: {e}")


def check_water_reminder():
    """
    Нагадування пити воду кожні 3 години — час залежить від зміни:
      Вихідний:    08:00 11:00 14:00 17:00 20:00
      Рання зміна: 05:00 08:00 11:00 14:00 17:00 (на роботі з 06:00)
      Нічна зміна: 14:00 17:00 20:00 23:00 02:00 (на роботі з 17:00)
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    if not (0 <= m < 5):
        return

    key = now_local.strftime("%Y-%m-%d-%H")
    gh_sent, gh_sha = _gh_get_sent()
    gh_water_key = f"water_{key}"
    if gh_sent is not None:
        if gh_sent.get(gh_water_key):
            return
    else:
        state = load_json_file(WATER_FILE, default={})
        if state.get(key):
            return

    try:
        from context import get_shift_from_calendar
        shift = get_shift_from_calendar().get("today", "free")
    except Exception:
        shift = "free"

    water_hours = {
        "free":  [8, 11, 14, 17, 20],
        "early": [5, 8, 11, 14, 17],
        "night": [14, 17, 20, 23, 2],
    }
    if h not in water_hours.get(shift, []):
        return

    send_telegram("💧 <b>Час випити воду!</b>\nВипий склянку води зараз 🥤")
    print(f"Water reminder sent at {h}:00 (shift={shift})")

    if gh_sent is not None:
        gh_sent[gh_water_key] = True
        _gh_save_sent(gh_sent, gh_sha)
    else:
        state = load_json_file(WATER_FILE, default={})
        state[key] = True
        save_json_file(WATER_FILE, state)


# ─── ПЛАН ТИЖНЯ (щопонеділка 8:00) ───────────────────────────────────────────

WEEK_PLAN_FILE = os.path.join(_DATA_DIR, "monitor_week_plan.json")

def check_weekly_plan():
    """Щопонеділка о 8:00 і щонеділі о 18:00 шле план на тиждень з календаря."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    is_monday_8  = (now_local.weekday() == 0 and now_local.hour == 8  and now_local.minute < 5)
    is_sunday_18 = (now_local.weekday() == 6 and now_local.hour == 18 and now_local.minute < 5)
    if not (is_monday_8 or is_sunday_18):
        return

    state = load_json_file(WEEK_PLAN_FILE, default={})
    today = now_local.strftime("%Y-%m-%d")
    key = f"{today}_{'sun18' if is_sunday_18 else 'mon8'}"
    if state.get(key):
        return

    try:
        token = _calendar_access_token()
        if not token:
            return
        headers = {"Authorization": f"Bearer {token}"}
        cal_id  = "novosadovoleg%40gmail.com"

        now_utc = datetime.now(timezone.utc)
        week_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end   = week_start + timedelta(days=7)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(week_start.isoformat())}"
            f"&timeMax={urllib.parse.quote(week_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=50"
        )
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        DAY_UA = ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"]
        by_day = {}
        for ev in events:
            summary = ev.get("summary","")
            if "нагадування" in summary.lower():
                continue
            start = ev["start"].get("dateTime") or ev["start"].get("date")
            try:
                dt = datetime.fromisoformat(start.replace("Z","+00:00")) + timedelta(hours=2)
                d  = dt.strftime("%Y-%m-%d")
                t  = dt.strftime("%H:%M")
            except:
                continue
            by_day.setdefault(d, []).append(f"{t} {esc(summary)}")

        lines = ["📅 <b>План на тиждень:</b>\n"]
        for i in range(7):
            day = (now_local + timedelta(days=i))
            d_str = day.strftime("%Y-%m-%d")
            d_label = f"{DAY_UA[day.weekday()]} {day.strftime('%d.%m')}"
            evs = by_day.get(d_str, [])
            if evs:
                lines.append(f"<b>{d_label}</b>")
                for e in evs[:5]:
                    lines.append(f"  • {e}")
            else:
                lines.append(f"<b>{d_label}</b> — вихідний")

        send_telegram("\n".join(lines))
        state[key] = True
        save_json_file(WEEK_PLAN_FILE, state)
        print("Weekly plan sent")

    except Exception as e:
        print(f"check_weekly_plan error: {e}")

# ─── ПЕРЕВІРКА ВИКОНАНИХ ПОДІЙ ────────────────────────────────────────────────

EVENT_DONE_FILE = os.path.join(_DATA_DIR, "monitor_event_done.json")

def check_event_done():
    """Кожні 5 хвилин: питає 'Виконано?' для ВСІХ подій що закінчились сьогодні
    і ще не отримали відповідь. Стійко до перезавантажень — dedup по event_id."""
    # Не питати вночі (00:00–07:00 місцевого)
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if now_local.hour < 7:
        return

    asked = set(load_json_file(EVENT_DONE_FILE, default=[]))

    try:
        token = _calendar_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        cal_id = "novosadovoleg%40gmail.com"

        now = datetime.now(timezone.utc)

        # Беремо всі події з початку сьогоднішнього дня (місцевого) до зараз
        day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(day_start.isoformat())}"
            f"&timeMax={urllib.parse.quote(now.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=50"
        )

        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        # Фільтруємо зміни і нічні — не питати про них
        SKIP_KEYWORDS = {"нічна", "рання зміна", "night shift", "early shift", "відпустка", "вихідний"}

        new_asked = list(asked)
        for ev in events:
            ev_id   = ev.get("id", "")
            summary = ev.get("summary", "(без назви)")
            end_raw = ev["end"].get("dateTime") or ev["end"].get("date")

            # Пропускаємо цілоденні події і зміни
            if not end_raw or "T" not in end_raw:
                continue
            if any(kw in summary.lower() for kw in SKIP_KEYWORDS):
                continue

            try:
                end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
            except Exception:
                continue

            # Подія має вже закінчитись (end_dt < now)
            if end_dt >= now:
                continue

            # Dedup по event_id + дата (один раз на подію на добу)
            today_str = now_local.strftime("%Y-%m-%d")
            key = f"done_{ev_id}_{today_str}"
            if key in asked:
                continue

            local_end = end_dt + timedelta(hours=2)
            t = local_end.strftime("%H:%M")

            s_lower = summary.lower()
            if "день народження" in s_lower or "birthday" in s_lower:
                emoji = "🎂"
            elif "зустріч" in s_lower or "meet" in s_lower or "дзвінок" in s_lower:
                emoji = "🤝"
            elif "лікар" in s_lower or "doctor" in s_lower:
                emoji = "🏥"
            else:
                emoji = "📅"

            text = (
                f"{emoji} <b>{esc(summary)}</b>\n"
                f"Планувалась до {t} — виконано?"
            )

            bot_token = os.environ.get("TELEGRAM_TOKEN", TELEGRAM_TOKEN)
            chat_id_tg = os.environ.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT)
            safe_key = key.replace("/", "_").replace("@", "_")[:60]

            payload = json.dumps({
                "chat_id": chat_id_tg,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [[
                        {"text": "✅ Виконано", "callback_data": f"evdone_yes_{safe_key}"},
                        {"text": "❌ Не виконано", "callback_data": f"evdone_no_{safe_key}"},
                        {"text": "⏭ Перенести",  "callback_data": f"evdone_skip_{safe_key}"},
                    ]]
                }
            }).encode()

            req2 = urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req2, timeout=15) as resp:
                resp.read()

            print(f"[event_done] asked: {summary} (ended {t})")
            new_asked.append(key)

        save_json_file(EVENT_DONE_FILE, new_asked[-500:])

    except Exception as e:
        print(f"check_event_done error: {e}")

# ─── ПІДСУМОК ДНЯ ────────────────────────────────────────────────────────────

DAY_SUMMARY_FILE = os.path.join(_DATA_DIR, "monitor_day_summary.json")

_DAY_SUMMARY_GH_URL = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/day_summary_sent.json"

def _day_summary_gh_check(date_str):
    """Повертає True якщо підсумок вже надіслано сьогодні (GitHub dedup). З retry."""
    import base64
    gh_token = os.environ.get("GITHUB_TOKEN", "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")
    if not gh_token:
        return False
    # Читаємо з конкретної гілки щоб уникнути stale кешу GitHub CDN
    url = _DAY_SUMMARY_GH_URL + f"?ref={_GH_DATA_BRANCH}&_ts={int(time.time())}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "Authorization": f"token {gh_token}",
                "Cache-Control": "no-cache",
                "User-Agent": "morning-report-bot"
            })
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
                content = json.loads(base64.b64decode(d["content"]).decode())
                return content.get("last") == date_str
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
    return False

def _day_summary_gh_mark(date_str):
    """Зберігає дату підсумку на GitHub."""
    import base64
    gh_token = os.environ.get("GITHUB_TOKEN", "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")
    if not gh_token:
        return
    # Get current SHA
    sha = None
    req = urllib.request.Request(_DAY_SUMMARY_GH_URL + f"?ref={_GH_DATA_BRANCH}", headers={
        "Authorization": f"token {gh_token}",
        "User-Agent": "morning-report-bot"
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            sha = json.loads(r.read()).get("sha")
    except Exception:
        pass
    content = base64.b64encode(json.dumps({"last": date_str}, indent=2).encode()).decode()
    body_dict = {
        "message": f"dedup: day summary sent {date_str}",
        "content": content,
        "branch": _GH_DATA_BRANCH,
    }
    if sha:
        body_dict["sha"] = sha
    body = json.dumps(body_dict).encode()
    req2 = urllib.request.Request(_DAY_SUMMARY_GH_URL, data=body, headers={
        "Authorization": f"token {gh_token}",
        "Content-Type": "application/json",
        "User-Agent": "morning-report-bot"
    }, method="PUT")
    try:
        urllib.request.urlopen(req2, timeout=8)
    except Exception as e:
        print(f"_day_summary_gh_mark error: {e}")

def check_day_summary():
    """
    🌙 RICH DAY SUMMARY — о 21:00 щодня.
    Містить: підсумок звичок + графік дня, ліки, вага, Apple Health,
             AI персональний підсумок з рекомендацією.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    # Час відправки залежить від зміни: нічна → 23:30, рання/вихідний → 21:30
    try:
        import sys as _sys; _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from meds import _get_today_shift_type as _gts
        _shift = _gts()
    except Exception:
        _shift = "weekend"
    send_hour, send_min = (23, 30) if _shift == "night" else (21, 30)

    if not (h == send_hour and send_min <= m < send_min + 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    # GitHub dedup (survives Railway restarts) — з retry проти race condition
    if _day_summary_gh_check(today):
        return

    DAY_UA = ["Понеділок","Вівторок","Середа","Четвер","П'ятниця","Субота","Неділя"]
    day_name = DAY_UA[now_local.weekday()]

    lines_out = []
    lines_out.append(f"🌙 <b>ПІДСУМОК ДНЯ — {day_name}, {now_local.strftime('%d.%m')}</b>")
    lines_out.append("")

    # ── Звички з візуалізацією ───────────────────────────────────────────────
    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from storage import load_habits as _lh
        habits_db = _lh()
        today_habits = habits_db.get(today, {})
    except Exception:
        today_habits = {}

    HEALTH_HABITS = [
        ("run",    "🏃", "Біг"),
        ("water",  "💧", "Вода 2л+"),
        ("shower", "🚿", "Хол.душ"),
        ("tea",    "🍵", "Трав.чай"),
        ("sauna",  "🧖", "Сауна"),
    ]

    done_count = 0
    habit_lines = []
    for hid, hico, hname in HEALTH_HABITS:
        v = today_habits.get(hid)
        if v is True:
            mark = "✅"; done_count += 1
        elif v is False:
            mark = "❌"
        else:
            mark = "⬜"
        habit_lines.append(f"{hico} {hname}  {mark}")

    # Прогрес-бар звичок
    total_h = len(HEALTH_HABITS)
    filled = int(done_count / total_h * 10) if total_h else 0
    bar = "🟩" * filled + "⬜" * (10 - filled)
    pct = int(done_count / total_h * 100) if total_h else 0

    if pct == 100: grade = "🏆 Ідеальний день!"
    elif pct >= 80: grade = "⭐️ Відмінно!"
    elif pct >= 60: grade = "👍 Непогано"
    elif pct >= 40: grade = "😐 Середньо"
    else: grade = "💤 Слабо"

    lines_out.append(f"💪 <b>Звички</b>  <code>[{bar}]</code> {done_count}/{total_h}  {grade}")
    for hl in habit_lines:
        lines_out.append(f"   {hl}")

    # Сон вчора
    sleep_v = today_habits.get("sleep")
    if sleep_v:
        s_ico = "😴✅" if sleep_v >= 7.5 else ("😴⚠️" if sleep_v >= 6 else "😴❌")
        lines_out.append(f"   😴 Сон  {sleep_v}г  {s_ico}")
    lines_out.append("")

    # ── Ліки ────────────────────────────────────────────────────────────────
    try:
        from storage import load_meds as _lmeds
        meds_db = _lmeds()
        meds_taken = meds_db.get(today)
        if meds_taken is True:
            lines_out.append("💊 <b>Armolopid Plus</b>  ✅ Прийнято")
        elif meds_taken is False:
            lines_out.append("💊 <b>Armolopid Plus</b>  ❌ <b>НЕ ПРИЙНЯТО!</b>")
        else:
            lines_out.append("💊 <b>Armolopid Plus</b>  ⬜ Не відмічено — прийняв?")
        lines_out.append("")
    except Exception:
        pass

    # ── Вага + мінітренд ────────────────────────────────────────────────────
    try:
        from storage import load_weight as _lw
        wdata = _lw()
        if wdata:
            recent = sorted(wdata.keys())[-7:]
            w_recent = [wdata[d] for d in recent if wdata.get(d)]
            last_w = wdata.get(today)
            if last_w:
                diff_goal = round(last_w - 78.0, 1)
                goal_str = f"до 78 кг: -{diff_goal}" if diff_goal > 0 else "🏆 ЦІЛЬ!"
                # Тренд
                if len(w_recent) >= 2:
                    delta = round(w_recent[-1] - w_recent[-2], 1)
                    trend = f"↗️+{delta}" if delta > 0 else f"↘️{delta}"
                else:
                    trend = ""
                lines_out.append(f"⚖️ <b>Вага сьогодні:</b> <b>{last_w} кг</b>  {trend}  ({goal_str})")
            elif w_recent:
                last_d = recent[-1]
                days_ago = (now_local.date() - datetime.strptime(last_d, "%Y-%m-%d").date()).days
                lines_out.append(f"⚖️ <b>Вага:</b> {w_recent[-1]} кг  <i>({days_ago} дн. тому — зважся!)</i>")
            lines_out.append("")
    except Exception:
        pass

    # ── Apple Health ─────────────────────────────────────────────────────────
    try:
        from storage import load_health as _lhealth
        health_db = _lhealth()
        td = health_db.get(today, {})
        if td:
            h_parts = []
            steps = td.get("steps")
            if steps:
                step_goal = 10000
                s_pct = int(steps / step_goal * 100)
                step_bar_f = int(s_pct / 100 * 8)
                step_bar = "🟩" * step_bar_f + "⬜" * (8 - step_bar_f)
                step_ico = "✅" if steps >= step_goal else ("⚠️" if steps >= 6000 else "❌")
                h_parts.append(f"👟 {steps:,} кроків {step_ico} {step_bar}")
            if td.get("sleep_hours"):
                sh = td["sleep_hours"]
                sh_ico = "✅" if sh >= 7.5 else ("⚠️" if sh >= 6 else "❌")
                h_parts.append(f"😴 Сон {sh}г {sh_ico}")
            if td.get("heart_rate"):
                h_parts.append(f"❤️ ЧСС {td['heart_rate']} bpm")
            if td.get("hrv"):
                h_parts.append(f"💓 HRV {td['hrv']}")
            if td.get("calories"):
                cal = td["calories"]
                cal_ico = "✅" if cal >= 400 else "📉"
                h_parts.append(f"🔥 {cal} ккал {cal_ico}")
            sc = td.get("health_score")
            if sc:
                sc_bar = "🟢" * int(sc/100*10) + "⬜" * (10 - int(sc/100*10))
                sc_ico = "🟢" if sc >= 75 else ("🟡" if sc >= 55 else "🔴")
                h_parts.append(f"{sc_ico} Score {sc}/100 [{sc_bar}]")

            if h_parts:
                lines_out.append("🍎 <b>Apple Health</b>")
                for hp in h_parts:
                    lines_out.append(f"   {hp}")
                lines_out.append("")
        else:
            lines_out.append("🍎 <b>Apple Health</b>  <i>немає даних — /зд для запису</i>")
            lines_out.append("")
    except Exception:
        pass

    # ── QWatch Pro ────────────────────────────────────────────────────────────
    try:
        import sys as _sys; _sys.path.insert(0, os.path.dirname(__file__))
        from qwatch import format_day_block as _qw_block
        qw = _qw_block(today)
        if qw:
            lines_out.append(qw)
            lines_out.append("")
    except Exception as _e:
        print(f"day summary qwatch error: {_e}")

    # ── AI персональний підсумок ──────────────────────────────────────────────
    try:
        # Збираємо контекст для AI
        extra_ctx = {
            "Звички сьогодні": f"{done_count}/{total_h}"
        }
        try:
            shift_s = get_shift_from_calendar()
            tom_shift = shift_s.get("tomorrow", "free")
            extra_ctx["Зміна завтра"] = tom_shift
        except Exception: pass
        try:
            cal_events = get_calendar()
            if cal_events:
                # Беремо тільки завтрашні події
                tom_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
                tom_events = [e for e in cal_events if e.get("date","").startswith(tom_str)]
                if tom_events:
                    extra_ctx["Події завтра"] = "; ".join(e.get("summary","?") for e in tom_events[:3])
        except Exception: pass

        ai_text = _ai_personal_message(
            f"Вечір {day_name}а, Олег закінчує день. "
            f"Зроби короткий підсумок дня на основі реальних даних (2-3 речення): "
            f"оціни звички, прогрес по вазі, і дай одну конкретну пораду на завтра. "
            f"Без загальних фраз, тільки факти і конкретика.",
            extra_ctx,
            max_tokens=200
        )
        if ai_text:
            lines_out.append(f"🤖 <i>{ai_text}</i>")
            lines_out.append("")
    except Exception as e:
        print(f"day summary AI error: {e}")

    # Коуч-фраза залежно від результату
    if pct == 100:
        lines_out.append("🏆 Ідеальний день — так тримати! Олег, ти топ 💪")
    elif pct >= 80:
        lines_out.append("⭐️ Майже ідеально. Ще трохи — і буде серія! 🔥")
    elif pct >= 60:
        lines_out.append("👍 Непогано — але ти можеш краще, знаємо обидва.")
    elif pct >= 40:
        lines_out.append("😐 Середній день. Завтра з ранку — чіткіше!")
    else:
        lines_out.append("💤 Сьогодні не вийшло — завтра новий шанс. Без самобичування, просто зробимо.")

    # Save-before-send (GitHub) — prevents duplicate on Railway restart
    _day_summary_gh_mark(today)
    send_telegram("\n".join(lines_out))
    print(f"Day summary sent: {today}")

    # ── Графік дня ──────────────────────────────────────────────────────────
    try:
        from charts import plot_day_dashboard as _plot_day
        chart_bytes = _plot_day(today)
        if chart_bytes:
            _send_photo_bytes(chart_bytes, f"📊 {day_name} {now_local.strftime('%d.%m')} — дашборд дня")
    except Exception as _e_chart_day:
        print(f"day chart error: {_e_chart_day}")

    # ── Кнопка "Додати в календар" після підсумку дня ────────────────────────
    try:
        from planner import _tg as _planner_tg_d, set_state as _planner_set_state_d
        _planner_tg_d("sendMessage", {
            "chat_id": TELEGRAM_CHAT,
            "text": (
                "📅 <b>Є щось на завтра?</b>\n"
                "<i>Запиши — я додам в календар і нагадаю</i>"
            ),
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "✏️ Записати в календар", "callback_data": "planner_write"},
                     {"text": "🛒 Що купити",           "callback_data": "shopping_add_item"}],
                    [{"text": "👍 Нічого",              "callback_data": "planner_skip"}]
                ]
            }
        })
    except Exception as _e_btn_d:
        print(f"planner button day_summary error: {_e_btn_d}")


def check_traffic_before_shift():
    """За 1 год до зміни надсилає стан трафіку в Кошіце."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    # О 05:00 (перед ранньою 06:00) і о 17:00 (перед нічною 17:00)
    if not ((h == 5 and m < 5) or (h == 17 and m < 5)):
        return

    state = load_json_file(TRAFFIC_ALERT_FILE, default={})
    key = now_local.strftime("%Y-%m-%d-%H")
    if state.get(key):
        return

    try:
        from traffic_kosice import format_traffic_report
        report = format_traffic_report()

        shift = "☀️ Рання зміна (06:00)" if h == 5 else "🌙 Нічна зміна (17:00)"
        msg = f"🚗 <b>Трафік перед зміною</b>\n{shift}\n\n{report}"
        send_telegram(msg)
        print(f"Traffic before shift sent at {h}:00")

        state[key] = True
        save_json_file(TRAFFIC_ALERT_FILE, state)

    except Exception as e:
        print(f"check_traffic_before_shift error: {e}")

# ─── НАГАДУВАННЯ ЗВАЖИТИСЬ ────────────────────────────────────────────────────

WEIGHT_REMIND_FILE = os.path.join(_DATA_DIR, "monitor_weight_remind.json")

def check_weight_reminder():
    """
    Нагадує зважитись:
    - 04:45 — якщо сьогодні рання зміна (06:00)
    - 11:11 — якщо вихідний (немає змін)
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    # Перевіряємо тільки у потрібні вікна
    is_445  = (h == 4 and 45 <= m <= 49)
    is_1111 = (h == 11 and 11 <= m <= 15)
    if not (is_445 or is_1111):
        return

    state = load_json_file(WEIGHT_REMIND_FILE, default={})
    key = f"{today}_{h}"
    if state.get(key):
        return

    try:
        # Перевіряємо календар — є зміна сьогодні?
        token = _calendar_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        cal_id = "novosadovoleg%40gmail.com"

        day_start_utc = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        day_end_utc   = now_local.replace(hour=23, minute=59, second=59, microsecond=0) - timedelta(hours=2)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(day_start_utc.isoformat())}"
            f"&timeMax={urllib.parse.quote(day_end_utc.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=10"
        )
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        has_early = any("рання" in ev.get("summary","").lower() for ev in events)
        has_shift = any("зміна" in ev.get("summary","").lower() for ev in events)
        is_day_off = not has_shift

        # 04:45 — тільки якщо є рання зміна
        if is_445 and not has_early:
            return

        # 11:11 — тільки якщо вихідний
        if is_1111 and not is_day_off:
            return

        msg = (
            "⚖️ <b>ЧАС ЗВАЖИТИСЬ</b>\n\n"
            "Зваж себе зараз і запиши в Apple Health.\n\n"
            "Потім надішли мені свою вагу, наприклад:\n"
            "<code>82.5</code>"
        )
        send_telegram(msg)
        print(f"Weight reminder sent at {h}:{m:02d}")

        state[key] = True
        save_json_file(WEIGHT_REMIND_FILE, state)

    except Exception as e:
        print(f"check_weight_reminder error: {e}")
# ─── HEALTH ALERT (HRV / СТРЕС) ──────────────────────────────────────────────

HEALTH_ALERT_FILE = os.path.join(_DATA_DIR, "monitor_health_alert.json")

def check_health_alert():
    """
    Після того як користувач вніс health дані — перевіряє:
    - HRV впав на 15+ від середнього за 7 днів → алерт
    - Стрес макс >= 60 → алерт
    - Стрес зріс на 15+ від середнього → алерт
    Надсилає не частіше 1 разу на день.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now_local.strftime("%Y-%m-%d")

    state = load_json_file(HEALTH_ALERT_FILE, default={})
    if state.get(today):
        return  # вже надсилали сьогодні

    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from storage import load_health as _lh
        health = _lh()
    except Exception as e:
        print(f"health_alert load error: {e}")
        return

    today_data = health.get(today, {})
    if not today_data:
        return  # немає даних за сьогодні — нічого перевіряти

    # Середнє за останні 7 днів (без сьогодні)
    past_days = sorted([d for d in health.keys() if d < today], reverse=True)[:7]
    past_data = [health[d] for d in past_days]

    alerts = []

    # ── HRV ──
    hrv_today = today_data.get("hrv")
    if hrv_today and past_data:
        hrv_vals = [d["hrv"] for d in past_data if d.get("hrv")]
        if hrv_vals:
            hrv_avg = sum(hrv_vals) / len(hrv_vals)
            hrv_drop = hrv_avg - hrv_today
            if hrv_drop >= 15:
                alerts.append(
                    f"💓 <b>HRV впав!</b>  {int(hrv_today)} ms  (серед. {int(hrv_avg)} ms, -<b>{int(hrv_drop)}</b>)\n"
                    f"   → Можливо перевтома або погана ніч. Більше відпочинку!"
                )

    # ── СТРЕС ──
    stress_today = today_data.get("stress_max")
    if stress_today:
        if stress_today >= 60:
            alerts.append(
                f"😤 <b>Високий стрес!</b>  {stress_today}/100\n"
                f"   → Рекомендую: дихальні вправи, прогулянка, менше екранів"
            )
        elif past_data:
            stress_vals = [d["stress_max"] for d in past_data if d.get("stress_max")]
            if stress_vals:
                stress_avg = sum(stress_vals) / len(stress_vals)
                stress_rise = stress_today - stress_avg
                if stress_rise >= 15:
                    alerts.append(
                        f"😤 <b>Стрес зріс!</b>  {stress_today}  (серед. {int(stress_avg)}, +<b>{int(stress_rise)}</b>)\n"
                        f"   → Зверни увагу на відновлення"
                    )

    # ── КРОКИ ──
    steps_today = today_data.get("steps")
    if steps_today and steps_today < 5000:
        alerts.append(
            f"👟 <b>Мало кроків!</b>  {steps_today:,}  (ціль 10,000)\n"
            f"   → Невелика прогулянка ввечері?"
        )

    # ── HEALTH SCORE ──
    score_today = today_data.get("health_score")
    if score_today and past_data:
        score_vals = [d["health_score"] for d in past_data if d.get("health_score")]
        if score_vals:
            score_avg = sum(score_vals) / len(score_vals)
            score_drop = score_avg - score_today
            if score_drop >= 15:
                alerts.append(
                    f"💚 <b>Health Score впав!</b>  {score_today}/100  (серед. {int(score_avg)}, -<b>{int(score_drop)}</b>)\n"
                    f"   → Провів поганий день? Аналізуй сон і стрес"
                )

    if not alerts:
        return

    msg = f"⚠️ <b>Health Alert</b>  {now_local.strftime('%d.%m')}\n\n"
    msg += "\n\n".join(alerts)
    send_telegram(msg)
    print(f"Health alert sent: {len(alerts)} alerts")

    state[today] = True
    save_json_file(HEALTH_ALERT_FILE, state)

# ─── НАГАДУВАННЯ ВНЕСТИ HEALTH ДАНІ ──────────────────────────────────────────

HEALTH_REMIND_FILE = os.path.join(_DATA_DIR, "monitor_health_remind.json")

def check_health_data_reminder():
    """
    Нагадування надіслати дані з QWatch Pro.
    Час залежить від зміни: нічна → 23:30, рання/вихідний → 21:30
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    # Визначаємо час залежно від зміни
    try:
        from meds import _get_today_shift_type as _gst_hr
        _shift_hr = _gst_hr()
    except Exception:
        _shift_hr = "weekend"
    send_hour, send_min = (23, 50) if _shift_hr == "night" else (21, 30)

    if not (h == send_hour and send_min <= m < send_min + 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    state = load_json_file(HEALTH_REMIND_FILE, default={})
    if state.get(today):
        return

    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from storage import load_health as _lh
        health = _lh()
        today_data = health.get(today, {})

        if today_data and today_data.get("steps"):
            return  # вже є дані

        msg = (
            "⌚ <b>Надішли дані з QWatch Pro!</b>\n\n"
            "Відкрий додаток QWatch Pro → зроби скрін або надішли вручну:\n\n"
            "<code>/зд [кроки] [сон] [ЧСС] [кал] [score]</code>"
        )
        send_telegram(msg)
        print(f"QWatch/Health data reminder sent (shift={_shift_hr}, time={send_hour}:{send_min:02d})")

        state[today] = True
        save_json_file(HEALTH_REMIND_FILE, state)

    except Exception as e:
        print(f"check_health_data_reminder error: {e}")

    # 4. Все інше — promo
    return "promo"


def check_crypto_weekly_summary():
    """Щонеділі о 19:00: % зміна BTC/ETH/AVAX/ONDO за тиждень + AI коментар."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.weekday() == 6 and now_local.hour == 19 and now_local.minute < 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    state = load_json_file(CRYPTO_WEEKLY_FILE, default={})
    if state.get("last") == today:
        return

    try:
        ids = ",".join(COINS.values())
        url = (
            f"https://api.coingecko.com/api/v3/coins/markets"
            f"?vs_currency=usd&ids={ids}&price_change_percentage=7d,24h"
        )
        raw = fetch_json(url)
        if not raw:
            return
        # convert list → dict by id
        data = {c["id"]: c for c in raw}

        # symbol order from COINS dict
        lines = []
        summary_parts = []
        for symbol, cg_id in COINS.items():
            coin = data.get(cg_id, {})
            price = coin.get("current_price")
            ch7d  = coin.get("price_change_percentage_7d_in_currency")
            ch24h = coin.get("price_change_percentage_24h")
            if price is None:
                continue

            arrow7 = "🟢" if (ch7d or 0) > 0 else "🔴"
            sign7  = "+" if (ch7d or 0) > 0 else ""
            lines.append(
                f"{arrow7} <b>{symbol}</b>: ${price:,.2f}  "
                f"7д: {sign7}{ch7d:.1f}%  24г: {'+' if (ch24h or 0)>0 else ''}{ch24h:.1f}%"
            )
            summary_parts.append(f"{symbol} {sign7}{ch7d:.1f}% за тиждень (${price:,.2f})")

        if not lines:
            return

        # AI коментар
        ai_comment = ""
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key and summary_parts:
            prompt = (
                "Ти фінансовий аналітик. Ось динаміка криптовалют за тиждень:\n"
                + "\n".join(summary_parts)
                + "\n\nДай короткий коментар (2-3 речення) українською: що відбулось на крипторинку цього тижня "
                  "і на що звернути увагу інвестору. Без зайвих слів, по суті."
            )
            try:
                payload = json.dumps({
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 800, "temperature": 0.7}
                }).encode()
                req = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    resp = json.loads(r.read())
                ai_comment = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as e:
                print(f"crypto weekly AI error: {e}")

        msg = f"📊 <b>Крипто підсумок тижня</b> ({today[5:]})\n\n"
        msg += "\n".join(lines)
        if ai_comment:
            msg += f"\n\n🤖 <i>{ai_comment}</i>"

        # Додаємо повний ETF/акції блок
        try:
            etf_full = _get_etf_prices(full=True)
            if etf_full:
                msg += f"\n\n{etf_full}"
        except Exception as _e_etf_w:
            print(f"[weekly etf block] {_e_etf_w}")

        send_telegram(msg)
        print("Crypto weekly summary sent")
        state["last"] = today
        save_json_file(CRYPTO_WEEKLY_FILE, state)

    except Exception as e:
        print(f"check_crypto_weekly_summary error: {e}")


# ─── NET WORTH НАГАДУВАННЯ (1-е число місяця 10:00) ──────────────────────────

NET_WORTH_FILE = os.path.join(_DATA_DIR, "monitor_net_worth.json")

def check_net_worth_reminder():
    """1-го числа кожного місяця о 10:00 — нагадування оновити net worth."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.day == 1 and now_local.hour == 10 and now_local.minute < 5):
        return

    month_key = now_local.strftime("%Y-%m")
    state = load_json_file(NET_WORTH_FILE, default={})
    if state.get("last") == month_key:
        return

    month_names = {
        1:"Січень",2:"Лютий",3:"Березень",4:"Квітень",5:"Травень",
        6:"Червень",7:"Липень",8:"Серпень",9:"Вересень",10:"Жовтень",
        11:"Листопад",12:"Грудень"
    }
    month_name = month_names[now_local.month]

    send_telegram(
        f"📊 <b>Net Worth — {month_name} {now_local.year}</b>\n\n"
        f"Початок нового місяця — час підбити підсумки!\n\n"
        f"Перевір та запиши:\n"
        f"💹 <b>Крипто</b> — BTC, ETH, AVAX, ONDO\n"
        f"🏦 <b>Банк</b> — поточний рахунок + заощадження\n"
        f"📈 <b>Інвестиції</b> — InterFin портфель\n"
        f"💰 <b>Готівка</b> — якщо є\n\n"
        f"Відстеження = мотивація рости! 💪"
    )
    print("Net worth reminder sent")
    state["last"] = month_key
    save_json_file(NET_WORTH_FILE, state)


# ─── ІНВЕСТИЦІЙНИЙ ДАЙДЖЕСТ (вівторок 08:00) ─────────────────────────────────

INVEST_DIGEST_FILE = os.path.join(_DATA_DIR, "monitor_invest_digest.json")

def check_investment_news_digest():
    """Щовівторка о 08:00: AI дайджест новин по інвестиціях/ETF/крипто-регуляції."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.weekday() == 1 and now_local.hour == 8 and now_local.minute < 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    state = load_json_file(INVEST_DIGEST_FILE, default={})
    if state.get("last") == today:
        return

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        return

    try:
        # Збираємо новини з Google News RSS
        import xml.etree.ElementTree as ET
        topics = [
            ("інвестиції ETF", "https://news.google.com/rss/search?q=investments+ETF+crypto&hl=uk&gl=UA&ceid=UA:uk"),
            ("crypto regulation", "https://news.google.com/rss/search?q=crypto+regulation+Bitcoin+ETF&hl=en&gl=US&ceid=US:en"),
            ("AVAX ONDO altcoin", "https://news.google.com/rss/search?q=Avalanche+AVAX+ONDO+altcoin&hl=en&gl=US&ceid=US:en"),
        ]

        all_titles = []
        for label, rss_url in topics:
            try:
                req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    tree = ET.fromstring(r.read())
                items = tree.findall(".//item")
                for item in items[:5]:
                    title_el = item.find("title")
                    if title_el is not None and title_el.text:
                        all_titles.append(title_el.text.strip())
            except Exception as e:
                print(f"RSS {label} error: {e}")

        if not all_titles:
            return

        news_block = "\n".join(f"- {t}" for t in all_titles[:15])
        prompt = (
            "Ти фінансовий аналітик. Ось заголовки новин за останні дні:\n\n"
            + news_block
            + "\n\nСклади короткий дайджест (3-4 речення) українською: що важливо знати "
              "приватному інвестору в крипто та ETF цього тижня. "
              "Виділи 1-2 ключові події. Без зайвих вступів, одразу по суті."
        )

        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 800, "temperature": 0.6}
        }).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
        digest = resp["candidates"][0]["content"]["parts"][0]["text"].strip()

        send_telegram(
            f"📰 <b>Інвестиційний дайджест</b> ({today[5:]})\n\n"
            f"{digest}\n\n"
            f"<i>🤖 AI підсумок по Google News</i>"
        )
        print("Investment news digest sent")
        state["last"] = today
        save_json_file(INVEST_DIGEST_FILE, state)

    except Exception as e:
        print(f"check_investment_news_digest error: {e}")


# ─── НАГАДУВАННЯ ПРО ІНТЕРВАЛЬНЕ ГОЛОДУВАННЯ (20:00 вільний день) ────────────

FASTING_FILE = os.path.join(_DATA_DIR, "monitor_fasting.json")

def check_fasting_reminder():
    """О 20:00 у вільний день: нагадування закінчити їсти (ціль — схуднення до 78 кг)."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.hour == 20 and now_local.minute < 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    state = load_json_file(FASTING_FILE, default={})
    if state.get("last") == today:
        return

    # Перевіряємо чи є зміна сьогодні
    has_shift = False
    try:
        token = _calendar_access_token()
        if token:
            tmin = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            tmax = tmin + timedelta(hours=24)
            url = (
                f"https://www.googleapis.com/calendar/v3/calendars/novosadovoleg%40gmail.com/events"
                f"?timeMin={urllib.parse.quote(tmin.isoformat())}"
                f"&timeMax={urllib.parse.quote(tmax.isoformat())}"
                f"&singleEvents=true&maxResults=10"
            )
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                events = json.loads(r.read()).get("items", [])
            has_shift = any("рання" in e.get("summary","").lower() or "нічна" in e.get("summary","").lower() for e in events)
    except Exception as e:
        print(f"fasting calendar check error: {e}")

    if has_shift:
        return  # в робочий день режим інший

    # Поточна вага для мотивації
    import storage as _wm_s; weight_data = _wm_s.load("weight_data.json") or _wm_s.load_weight() or {}
    weight_note = ""
    if weight_data:
        last_w = sorted(weight_data.items())[-1][1]
        to_goal = last_w - 78.0
        if to_goal > 0:
            weight_note = f"\n\n⚖️ До цілі 78 кг ще: <b>{to_goal:.1f} кг</b> — кожен день рахується!"

    send_telegram(
        "🕗 <b>Час зупинитись з їжею!</b>\n\n"
        "Якщо практикуєш <b>інтервальне голодування 16:8</b>:\n"
        "• Останній прийом їжі о 20:00\n"
        "• Наступний — о 12:00 завтра\n"
        "• Можна: вода, чай без цукру\n\n"
        "💪 Дотримання вікна — ключ до схуднення!"
        + weight_note
    )
    print("Fasting reminder sent")
    state["last"] = today
    save_json_file(FASTING_FILE, state)


# ─── ПОГОДА ПЕРЕД ЗМІНОЮ (за 1.5г до початку) ───────────────────────────────

PRE_SHIFT_WEATHER_FILE = os.path.join(_DATA_DIR, "monitor_pre_shift_weather.json")

def check_pre_shift_weather():
    """За 1.5 години до зміни: погода на час дороги + чи потрібна куртка/парасоля."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    # Рання зміна о 05:00 → нагадування о 03:30
    # Нічна зміна о 17:00 → погода вже включена в pre_night (check_smart_notifications 16:30)
    is_pre_early = (h == 3 and 28 <= m <= 31)

    if not is_pre_early:
        return

    key = "pre_early"
    shift_time = "05:00"

    state = load_json_file(PRE_SHIFT_WEATHER_FILE, default={})
    if state.get(key) == today:
        return

    # Перевіряємо чи є відповідна зміна сьогодні
    has_shift = False
    try:
        token = _calendar_access_token()
        if token:
            tmin = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            tmax = tmin + timedelta(hours=24)
            url = (
                f"https://www.googleapis.com/calendar/v3/calendars/novosadovoleg%40gmail.com/events"
                f"?timeMin={urllib.parse.quote(tmin.isoformat())}"
                f"&timeMax={urllib.parse.quote(tmax.isoformat())}"
                f"&singleEvents=true&maxResults=10"
            )
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                events = json.loads(r.read()).get("items", [])

            shift_word = "рання"
            has_shift = any(shift_word in e.get("summary","").lower() for e in events)
    except Exception as e:
        print(f"pre_shift_weather calendar error: {e}")

    if not has_shift:
        return

    try:
        # Погода на конкретну годину через open-meteo hourly
        shift_hour = 5
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=48.7163&longitude=21.2611"
            f"&hourly=temperature_2m,apparent_temperature,precipitation_probability,weathercode,windspeed_10m"
            f"&forecast_days=1&timezone=Europe%2FPrague"
        )
        data = fetch_json(url)
        if not data:
            return

        hourly = data.get("hourly", {})
        times  = hourly.get("time", [])
        temps  = hourly.get("temperature_2m", [])
        feels  = hourly.get("apparent_temperature", [])
        precip = hourly.get("precipitation_probability", [])
        codes  = hourly.get("weathercode", [])
        winds  = hourly.get("windspeed_10m", [])

        # Знаходимо потрібний час
        idx = None
        for i, t in enumerate(times):
            if t.endswith(f"T{shift_hour:02d}:00"):
                idx = i
                break

        if idx is None or idx >= len(temps):
            return

        WMO = {
            0:"☀️ Ясно",1:"🌤 Перев.ясно",2:"⛅️ Хмарно",3:"☁️ Похмуро",
            45:"🌫 Туман",51:"🌦 Мряка",61:"🌧 Дощ",63:"🌧 Дощ",65:"🌧 Сильний дощ",
            71:"❄️ Сніг",80:"🌦 Злива",81:"🌦 Злива",95:"⛈ Гроза",96:"⛈ Гроза"
        }

        temp   = temps[idx]
        feel   = feels[idx] if idx < len(feels) else temp
        rain_p = precip[idx] if idx < len(precip) else 0
        code   = codes[idx] if idx < len(codes) else 0
        wind   = winds[idx] if idx < len(winds) else 0
        desc   = WMO.get(code, "—")

        # Рекомендації
        tips = []
        if rain_p >= 50 or code in {51,53,55,61,63,65,80,81,82,95,96,99}:
            tips.append("☂️ Візьми парасолю!")
        if feel < 10:
            tips.append("🧥 Тепла куртка — на вулиці холодно")
        elif feel < 16:
            tips.append("🧥 Легка куртка не завадить")
        if wind >= 30:
            tips.append("💨 Сильний вітер")

        tips_text = "\n".join(tips) if tips else "✅ Погода нормальна — нічого особливого"

        send_telegram(
            f"🌤 <b>Погода на дорогу до роботи</b> ({shift_time})\n\n"
            f"{desc}  {temp:.0f}°C (відчувається {feel:.0f}°C)\n"
            f"💧 Дощ: {rain_p}%  💨 Вітер: {wind:.0f} км/г\n\n"
            f"{tips_text}"
        )
        print(f"Pre-shift weather sent for {shift_time}")
        state[key] = today
        save_json_file(PRE_SHIFT_WEATHER_FILE, state)

    except Exception as e:
        print(f"check_pre_shift_weather error: {e}")


# ─── СТРІК НАВЧАННЯ ІНВЕСТИЦІЯМ ──────────────────────────────────────────────

LEARNING_STREAK_FILE = os.path.join(_DATA_DIR, "monitor_learning_streak.json")

def check_learning_streak():
    """
    Якщо 2+ дні підряд немає запису в habits про навчання → нагадування.
    Перевіряємо щодня о 18:00.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.hour == 18 and now_local.minute < 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    state = load_json_file(LEARNING_STREAK_FILE, default={})
    if state.get("last") == today:
        return

    try:
        import sys as _sys
        _sys.path.insert(0, _DIR)
        from storage import load_habits as _lh
        habits = _lh()
        if not habits:
            return

        # Шукаємо кількість днів без навчання підряд
        days_without = 0
        for i in range(1, 8):  # перевіряємо до 7 днів назад
            day = (now_local - timedelta(days=i)).strftime("%Y-%m-%d")
            day_data = habits.get(day, {})

            # Перевіряємо наявність запису про навчання
            # Habits зазвичай мають поля: learning, study, навчання тощо
            has_learning = (
                day_data.get("learning") or
                day_data.get("study") or
                day_data.get("навчання") or
                day_data.get("invest_study") or
                day_data.get("education")
            )
            if has_learning:
                break
            days_without += 1

        if days_without >= 2:
            msg = (
                f"📚 <b>Навчання інвестиціям — {days_without} дні без занять!</b>\n\n"
                f"⚠️ Не переривай streak!\n\n"
                f"Навіть 15-20 хвилин на день:\n"
                f"• Курс від Maroš Sivák / InterFin\n"
                f"• Читання статті про ETF або крипто\n"
                f"• Перегляд відео по фінансах\n\n"
                f"💡 <i>Консистентність > інтенсивність</i>"
            )
            send_telegram(msg)
            print(f"Learning streak reminder sent: {days_without} days without study")

        state["last"] = today
        save_json_file(LEARNING_STREAK_FILE, state)

    except Exception as e:
        print(f"check_learning_streak error: {e}")


# ─── SMART CONTEXT-AWARE NOTIFICATIONS ───────────────────────────────────────

SMART_NOTIF_FILE = os.path.join(_DATA_DIR, "monitor_smart_notif.json")

def check_smart_notifications():
    """
    🧠 SMART NOTIFICATIONS — щохвилинна перевірка.
    Ситуативні сповіщення прив'язані до зміни + прогрес до цілей.

    ПРАВИЛО: спочатку читаємо КАЛЕНДАР — і тільки тоді вирішуємо що і коли писати.
    Якщо Олег спить — нічого не надсилаємо (крім pre_early о 04:30).
    """
    import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    try:
        from context import get_context, get_shift_from_calendar, get_status, should_notify, should_notify_low_priority
    except Exception as e:
        print(f"context import error: {e}"); return

    try:
        now_local = datetime.now(timezone.utc) + timedelta(hours=2)
        h = now_local.hour
        m = now_local.minute
        today = now_local.strftime("%Y-%m-%d")
        state = load_json_file(SMART_NOTIF_FILE, default={})

        def sent(key): return state.get(key) == today
        def mark(key):
            state[key] = today
            save_json_file(SMART_NOTIF_FILE, state)

        # ── КРОК 1: ЗАВЖДИ читаємо календар першим ───────────────────────────
        shift_info     = get_shift_from_calendar()
        today_shift    = shift_info.get("today", "free")
        tomorrow_shift = shift_info.get("tomorrow", "free")
        current_status = get_status(shift_info)

        # pre_early дозволений навіть якщо статус "sleeping" (будимо на зміну)
        # Для решти — якщо спить, нічого не надсилаємо
        is_pre_early_window = (today_shift == "early" and h == 4 and 30 <= m < 35)
        if current_status == "sleeping" and not is_pre_early_window:
            return

        # ── 1. ПІДЙОМ ПЕРЕД РАННЬОЮ (04:30) ───────────────────────────────
        if today_shift == "early" and h == 4 and 30 <= m < 35 and not sent("pre_early"):
            # Погода швидко
            weather_ctx = ""
            try:
                wkey = os.environ.get("WEATHER_API_KEY","")
                if wkey:
                    url_w = f"https://api.openweathermap.org/data/2.5/weather?q=Kosice&appid={wkey}&units=metric&lang=uk"
                    req_w = urllib.request.Request(url_w, headers={"User-Agent":"bot"})
                    with urllib.request.urlopen(req_w, timeout=5) as r:
                        wd = json.loads(r.read())
                    temp = round(wd["main"]["temp"])
                    desc = wd["weather"][0]["description"]
                    weather_ctx = f"{temp}°C, {desc}"
            except Exception: pass

            ai_txt = _ai_personal_message(
                "Олег прокидається о 04:30 на ранню зміну (06:00–18:00). "
                "Нагадай про сніданок, ліки Armolopid, підбадьори конкретно на основі реальних даних.",
                {"Погода в Кошіце": weather_ctx} if weather_ctx else None,
                max_tokens=180
            )
            # Час до виходу
            now_l2 = datetime.now(timezone.utc) + timedelta(hours=2)
            mins_to_go = max(0, (6*60 - (now_l2.hour*60 + now_l2.minute)) - 30)
            weather_line = f"\n🌡 <b>{weather_ctx}</b>" if weather_ctx else ""
            header = (
                f"⏰ <b>ПІДЙОМ!</b>  ·  04:30{weather_line}\n"
                f"┌─────────────────────────┐\n"
                f"│  ☀️ Рання зміна  06:00–18:00  │\n"
                f"│  🚶 Вихід приблизно о 05:30  │\n"
                f"└─────────────────────────┘\n"
                f"💊 Armolopid  ·  🍳 Сніданок  ·  👕 Одяг\n\n"
            )
            send_telegram(header + (ai_txt or "Вперед — ти впораєшся!"))
            mark("pre_early")

        # ── 2. ПІСЛЯ РАННЬОЇ (18:15) ───────────────────────────────────────
        elif today_shift == "early" and h == 18 and 15 <= m < 20 and not sent("post_early"):
            habits_ctx = ""
            try:
                from storage import load_habits as _lh
                db = _lh()
                td_habits = db.get(today, {})
                done = sum(1 for k in ["run","water","shower","tea"] if td_habits.get(k) is True)
                habits_ctx = f"{done}/4 звичок виконано сьогодні"
            except Exception: pass

            ai_txt = _ai_personal_message(
                "Олег тільки що прийшов додому після ранньої зміни (06:00–18:00). "
                "Запитай про вагу (зважитись), порадь чи варто бігти зараз, "
                "оціни день конкретно на основі даних. 2-3 речення, по суті.",
                {"Звички сьогодні": habits_ctx} if habits_ctx else None,
                max_tokens=200
            )
            habits_bar = ""
            if habits_ctx:
                done_n = int(habits_ctx[0]) if habits_ctx[0].isdigit() else 0
                bars_post = "✅" * done_n + "⬜" * (4 - done_n)
                habits_bar = f"\n{bars_post} {habits_ctx}"
            header = (
                f"🏠 <b>РАННЯ ЗМІНА ЗАВЕРШЕНА</b>\n"
                f"{'─' * 26}\n"
                f"12 годин відпрацьовано 💪{habits_bar}\n\n"
                f"📋 Що зараз:\n"
                f"⚖️ Зважся  ·  🍽 Поїж  ·  🛁 Душ\n\n"
            )
            send_telegram(header + (ai_txt or "Ти сьогодні молодець — відпочивай!"))
            mark("post_early")

        # ── 3. ПІДГОТОВКА ДО НІЧНОЇ (16:30) ──────────────────────────────
        elif today_shift == "night" and h == 16 and 30 <= m < 35 and not sent("pre_night"):
            ai_txt = _ai_personal_message(
                "Олег готується до нічної зміни (17:00–05:00), старт через 1.5 години. "
                "Нагадай поїсти зараз (до 06:00 не буде можливості), прийняти Armolopid, "
                "коротко підбадьори. Дуже конкретно, 2-3 речення.",
                None,
                max_tokens=180
            )
            header = (
                f"🌙 <b>НІЧНА ЗМІНА — ЧЕРЕЗ 1.5 ГОДИНИ</b>\n"
                f"{'═' * 28}\n"
                f"  🕕 Старт: 17:00  ·  🕕 Фініш: 05:00\n"
                f"  🚶 Вихід о 17:25–17:30\n"
                f"{'─' * 28}\n"
                f"☑️ Поїж зараз  ·  💊 Armolopid  ·  ☕ Термос\n\n"
            )
            send_telegram(header + (ai_txt or "Хорошої зміни! Ти справишся 🌙"))
            mark("pre_night")

        # ── 4. ПІСЛЯ НІЧНОЇ (06:15) ───────────────────────────────────────
        elif today_shift == "night" and h == 6 and 15 <= m < 20 and not sent("post_night"):
            ai_txt = _ai_personal_message(
                "Олег тільки що закінчив нічну зміну (17:00–05:00) і йде додому. "
                "Скажи йому легко поїсти, лягти спати, не гортати телефон — конкретно і коротко. "
                "Оціни його зусилля на основі реальних даних (вага, звички). 2-3 речення.",
                None,
                max_tokens=180
            )
            header = (
                f"😴 <b>НІЧНА ЗАВЕРШЕНА!</b>  06:15\n"
                f"{'━' * 26}\n"
                f"  12 нічних годин ✅  Йди додому!\n"
                f"{'─' * 26}\n"
                f"🍳 Легкий сніданок  ·  📵 Телефон відклади  ·  😴 СОН\n\n"
            )
            send_telegram(header + (ai_txt or "Сон після ночі — пріоритет №1. Все інше зачекає."))
            mark("post_night")

        # ── 5. AI ПОРАДА (11:00, 15:00, 20:30 — вільний день)
        # Часи зміщені щоб НЕ збігались з morning_context (08:30/10:00),
        # crypto_morning (09:10), day_summary (19:00), mood (21:30)
        ai_slots = {
            11: ("💡 Порада на день", "Олег вдома у вільний день, ранок минув. Дай ОДНУ конкретну дію на найближчі години — для схуднення (ціль 78 кг) або здоров'я. 1-2 речення, конкретно."),
            15: ("☀️ Порада на другу половину дня", "Олег вдома в середині дня. Дай одну ідею — що зробити для здоров'я або продуктивності наступні 2 години. Коротко і конкретно."),
            20: ("🌙 Вечірня порада", "Вечір вільного дня Олега. 1-2 речення: коротка оцінка дня і одна порада перед сном (схуднення/здоров'я/фінанси). По суті, без загальних слів."),
        }
        if today_shift == "free" and h in ai_slots and 30 <= m < 35:
            akey = f"ai_tip_{h}"
            if not sent(akey):
                label, prompt_text = ai_slots[h]
                gemini_key = os.environ.get("GEMINI_API_KEY","")
                if gemini_key:
                    try:
                        # Додаємо контекст ваги
                        w_context = ""
                        try:
                            from storage import load_weight as _lw
                            wdata = _lw()
                            if wdata:
                                last_k = sorted(wdata.keys())[-1]
                                w_context = f" Остання вага: {wdata[last_k]} кг."
                        except Exception: pass

                        import uuid as _uuid_slot
                        slot_seed = str(_uuid_slot.uuid4())[:8]
                        cal_ev = _get_calendar_events_text()
                        cal_hint = f" Календар: {cal_ev}." if cal_ev and cal_ev != "нічого не заплановано" else ""
                        full_prompt = f"{prompt_text}{w_context}{cal_hint} [id:{slot_seed}]"
                        payload = json.dumps({"contents":[{"parts":[{"text":full_prompt}]}],"generationConfig":{"maxOutputTokens":600,"temperature":0.95}}).encode()
                        req_ai = urllib.request.Request(
                            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                            data=payload, headers={"Content-Type":"application/json"}, method="POST"
                        )
                        with urllib.request.urlopen(req_ai, timeout=20) as r:
                            resp = json.loads(r.read())
                        tip = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
                        send_telegram(f"{label}\n\n{tip}")
                        mark(akey)
                    except Exception as e:
                        print(f"smart notif AI error: {e}")

        # ── 6. ПЛАН НА ЗАВТРА (22:00 якщо є зміна) ───────────────────────
        if tomorrow_shift in ("early", "night") and h == 22 and 0 <= m < 5 and not sent("tomorrow_plan"):
            if tomorrow_shift == "early":
                send_telegram(
                    f"🌙 <b>ВЕЧІРНІЙ ЧЕКЛІСТ</b>\n"
                    f"╔══════════════════════╗\n"
                    f"║  ☀️ Завтра РАННЯ зміна  ║\n"
                    f"║      06:00 → 18:00      ║\n"
                    f"╚══════════════════════╝\n\n"
                    f"Зроби зараз:\n"
                    f"  😴 Лягай до 22:30\n"
                    f"  ⏰ Поставь будильник 04:30\n"
                    f"  👕 Приготуй одяг і їжу\n"
                    f"  💊 Armolopid на ранок (поруч)\n\n"
                    f"<i>Хороший сон = успішна зміна!</i>"
                )
            else:
                send_telegram(
                    f"🌙 <b>ВЕЧІРНІЙ ЧЕКЛІСТ</b>\n"
                    f"╔══════════════════════╗\n"
                    f"║  🌙 Завтра НІЧНА зміна  ║\n"
                    f"║      17:00 → 05:00      ║\n"
                    f"╚══════════════════════╝\n\n"
                    f"Підготовка:\n"
                    f"  😴 Поспи вдень якщо зможеш\n"
                    f"  🍽 Поїж о 16:30–17:00 (до 06:00 більше не буде)\n"
                    f"  ☕ Підготуй термос з чаєм\n"
                    f"  💊 Armolopid після обіду\n\n"
                    f"<i>Ти впораєшся, нічна — твій режим 💪</i>"
                )
            mark("tomorrow_plan")

        # ── 7. ПРОГРЕС ДО 78 КГ (щосереди о 12:00) ───────────────────────
        if today_shift == "free" and now_local.weekday() == 2 and h == 12 and 0 <= m < 5 and not sent("weight_progress"):
            try:
                from storage import load_weight as _lw
                wdata = _lw()
                if wdata:
                    sorted_keys = sorted(wdata.keys())
                    if len(sorted_keys) >= 2:
                        last_w = wdata[sorted_keys[-1]]
                        first_w = wdata[sorted_keys[0]]
                        to_goal = round(last_w - 78.0, 1)
                        total_lost = round(first_w - last_w, 1)
                        if to_goal > 0:
                            # Графік останніх 5 вимірювань
                            recent_5 = sorted_keys[-5:]
                            w_vals = [wdata[d] for d in recent_5]
                            w_min = min(w_vals) - 0.3
                            w_max = max(w_vals) + 0.3
                            blocks = ["⬜","🟦","🟦","🟩","🟩","🟨","🟧","🟥"]
                            bars = []
                            for v in w_vals:
                                b = int((v - w_min) / max(w_max - w_min, 0.1) * 7)
                                bars.append(blocks[max(0, min(7, b))])
                            trend = "↗️" if w_vals[-1] > w_vals[-2] else "↘️" if w_vals[-1] < w_vals[-2] else "→"
                            send_telegram(
                                f"⚖️ <b>Прогрес до цілі 78 кг</b>\n\n"
                                f"Зараз: <b>{last_w} кг</b>  {trend}\n"
                                f"До цілі: <b>{to_goal} кг</b>\n"
                                f"Всього скинуто: {total_lost} кг\n\n"
                                f"<code>{''.join(bars)}</code>  (останні 5 вимірювань)\n\n"
                                f"{'🎯 Ще трохи!' if to_goal < 2 else ('💪 Продовжуй!' if to_goal < 5 else '🔥 Ти на шляху!')}"
                            )
                            mark("weight_progress")
            except Exception as e:
                print(f"weight progress error: {e}")

        # ── 8. НАГАДУВАННЯ ХОЛОДНИЙ ДУШ ──────────────────────────────────
        # вихідний → 11:00, рання → 05:10, нічна → 17:00
        shower_time = None
        if today_shift == "free" and h == 11 and 0 <= m < 5:
            shower_time = "shower_remind"
        elif today_shift == "early" and h == 5 and 10 <= m < 15:
            shower_time = "shower_remind"
        elif today_shift == "night" and h == 17 and 0 <= m < 5:
            shower_time = "shower_remind"

        if shower_time and not sent("shower_remind"):
            # Перевіряємо чи вже позначений сьогодні
            already_done = False
            try:
                from storage import load_habits as _lsh
                _hdb = _lsh()
                already_done = _hdb.get(today, {}).get("shower") is True
            except Exception:
                pass

            if not already_done:
                _tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                _tg_chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
                if _tg_token and _tg_chat:
                    _shower_payload = json.dumps({
                        "chat_id": _tg_chat,
                        "text": "🚿 <b>Холодний душ!</b>\n\nЗроби зараз — 30 секунд холодної води.\nКортизол ↓  Дофамін ↑  Імунітет ↑",
                        "parse_mode": "HTML",
                        "reply_markup": {"inline_keyboard": [[
                            {"text": "✅ Зробив",    "callback_data": "habit_yes_shower"},
                            {"text": "❌ Пропустив", "callback_data": "habit_no_shower"},
                        ]]}
                    }).encode()
                    _shower_req = urllib.request.Request(
                        f"https://api.telegram.org/bot{_tg_token}/sendMessage",
                        data=_shower_payload,
                        headers={"Content-Type": "application/json"},
                        method="POST"
                    )
                    try:
                        urllib.request.urlopen(_shower_req, timeout=10)
                        print("Shower reminder sent")
                    except Exception as _se:
                        print(f"Shower remind send error: {_se}")
                mark("shower_remind")

    except Exception as e:
        print(f"check_smart_notifications error: {e}")


MORNING_CTX_FILE = os.path.join(_DATA_DIR, "monitor_morning_ctx.json")

def check_morning_context():
    """
    Розумний ранковий брифінг — знає тип дня і адаптує зміст + час:
      рання зміна  → о 05:00 (перед виходом)
      нічна зміна  → о 10:00 (після сну)
      вихідний     → о 08:30

    ЛОГІКА:
    1. Спочатку читає Google Calendar — визначає shift
    2. Підлаштовує час відправки і зміст під тип дня
    3. AI-порада базується на реальних подіях календаря + даних
    4. Dedup через GitHub — не дублює при Railway restart
    5. Uniq seed — кожне повідомлення нове, не повторюється
    """
    import sys, uuid as _uuid
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    state = load_json_file(MORNING_CTX_FILE, default={})
    # GitHub dedup — стійкий до Railway restarts
    gh_morning, gh_morning_sha = _gh_get_json("monitor_morning_ctx.json")
    if gh_morning.get("last") == today:
        print(f"Morning context already sent today ({today}), skipping.")
        return
    if state.get("last") == today:
        return

    # ── КРОК 1: Читаємо календар ПЕРШИМ ──────────────────────────────────────
    try:
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        shift = shift_info.get("today", "free")
    except Exception:
        shift = "free"

    # Час відправки залежно від типу дня
    # after_night — о 11:00 (людина спить вранці після нічної)
    trigger = {"early": 5, "night": 10, "after_night": 11, "free": 8}.get(shift, 8)
    if not (h == trigger and 0 <= m < 5):
        return

    try:
        # ── КРОК 2: Збираємо події з календаря ──────────────────────────────
        cal_events_raw = _get_calendar_events_text()  # "HH:MM Подія; HH:MM Подія2"
        cal_full = get_calendar()  # відформатований блок для повідомлення

        # ── КРОК 3: Погода ───────────────────────────────────────────────────
        try:
            weather = get_weather()
            weather_short = weather.split("\n")[0] if weather else ""
        except Exception:
            weather_short = ""

        # ── КРОК 4: Крипто ───────────────────────────────────────────────────
        crypto_text = ""
        try:
            ids = ",".join(COINS.values())
            url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}&price_change_percentage=24h"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                raw = json.loads(r.read())
            crypto_lines = []
            for c in raw:
                sym = c["symbol"].upper()
                price = c["current_price"]
                ch = c.get("price_change_percentage_24h") or 0
                icon = "🟢" if ch > 0 else "🔴"
                sign = "+" if ch > 0 else ""
                crypto_lines.append(f"{icon} {sym} ${price:,.0f} ({sign}{ch:.1f}%)")
            crypto_text = "  ".join(crypto_lines)
        except Exception:
            pass

        # ── КРОК 5: AI-порада з урахуванням КАЛЕНДАРЯ ────────────────────────
        gemini_key = os.environ.get("GEMINI_API_KEY", "AIzaSyDRXcGERTNILIEDKbmgTKSXUuiwt1oKeGM")
        ai_tip = ""
        if gemini_key:
            try:
                shift_labels = {
                    "early": "рання зміна (06:00–18:00) — сьогодні на роботу",
                    "night": "нічна зміна (17:00–05:00) — сьогодні ввечері на роботу",
                    "after_night": "після нічної зміни — вчора ніч відпрацював, сьогодні відновлення і відпочинок",
                    "free":  "вихідний день — вільний графік"
                }

                weight_ctx = ""
                try:
                    from storage import load_weight as _lw
                    wdata = _lw()
                    if wdata:
                        last_key = sorted(wdata.keys())[-1]
                        weight_ctx = f"Вага: {wdata[last_key]} кг (ціль 78, залишилось {wdata[last_key]-78:.1f} кг)."
                except Exception:
                    pass

                health_ctx = ""
                try:
                    from storage import load_health as _lh
                    hdata = _lh()
                    if hdata:
                        last_hkey = sorted(hdata.keys())[-1]
                        hd = hdata[last_hkey]
                        parts = []
                        if hd.get("steps"): parts.append(f"кроки {hd['steps']}")
                        if hd.get("sleep_hours"): parts.append(f"сон {hd['sleep_hours']}г")
                        if hd.get("hrv"): parts.append(f"HRV {hd['hrv']}")
                        if parts: health_ctx = f"Вчора: {', '.join(parts)}."
                except Exception:
                    pass

                habits_ctx = ""
                try:
                    from habits import load_data as _lhab
                    hab_db = _lhab()
                    yest = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
                    yd = hab_db.get(yest, {})
                    done_h = [k for k in ["run","water","shower","tea"] if yd.get(k) is True]
                    if done_h: habits_ctx = f"Звички вчора: {', '.join(done_h)}."
                except Exception:
                    pass

                day_names = ['Пн','Вт','Ср','Чт','Пт','Сб','Нд']
                msg_seed = str(_uuid.uuid4())[:8]

                cal_ctx = (
                    f"Заплановано сьогодні: {cal_events_raw}"
                    if cal_events_raw and cal_events_raw != "нічого не заплановано"
                    else "Подій у календарі сьогодні немає"
                )

                prompt = (
                    f"Ти персональний асистент Олега (Кошіце, Словаччина). "
                    f"Сьогодні {day_names[now_local.weekday()]} {now_local.strftime('%d.%m.%Y')}, "
                    f"{now_local.strftime('%H:%M')}. [id:{msg_seed}]\n"
                    f"Тип дня: {shift_labels.get(shift,'вихідний')}.\n"
                    f"{cal_ctx}\n"
                    f"{weight_ctx} {health_ctx} {habits_ctx}\n"
                    f"Погода: {weather_short if weather_short else 'невідома'}.\n\n"
                    f"Напиши ПЕРСОНАЛЬНЕ привітання і конкретну пораду на ЦЕЙ день (2-3 речення). "
                    f"ОБОВ'ЯЗКОВО: якщо є події в календарі — згадай їх і дай пораду відповідно. "
                    f"Якщо рання/нічна зміна — враховуй це у пораді. "
                    f"Якщо вихідний і нема подій — запропонуй конкретне (біг, ціль по вазі і т.д.). "
                    f"Реальні цифри, конкретика, без загальних фраз. Мова: українська."
                )
                payload = json.dumps({
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.95},
                }).encode()
                req2 = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    data=payload, headers={"Content-Type": "application/json"}, method="POST"
                )
                with urllib.request.urlopen(req2, timeout=15) as r:
                    resp = json.loads(r.read())
                ai_tip = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as e:
                print(f"morning context AI error: {e}")

        # ── КРОК 6: Будуємо повідомлення ─────────────────────────────────────
        greetings = {
            "early":       "☀️ Доброго ранку!",
            "night":       "🌞 З добрим ранком, Олеже!",
            "after_night": "😴 Привіт, Олеже. Після нічної — не поспішай.",
            "free":        "🌅 Доброго ранку, Олеже!"
        }
        greeting = greetings.get(shift, "🌅 Доброго ранку!")

        shift_info_text = {
            "early":       "💼 Сьогодні <b>рання зміна</b> — виходити о 05:30",
            "night":       "🌙 Сьогодні <b>нічна зміна</b> — виходити о 17:30",
            "after_night": "🛋 Вчора була <b>нічна зміна</b> — сьогодні відпочиваєш. Нічого зайвого не планувати.",
            "free":        "🏖 Сьогодні <b>вихідний</b> — твій день!"
        }.get(shift, "")

        msg = f"{greeting}\n\n{shift_info_text}\n\n"
        if weather_short:
            msg += f"🌤 {weather_short}\n\n"
        msg += f"📅 <b>Календар на сьогодні:</b>\n{cal_full}\n\n"
        if crypto_text:
            msg += f"💹 {crypto_text}\n\n"
        if ai_tip:
            msg += f"💡 <i>{ai_tip}</i>"

        # ── КРОК 7: Зберігаємо ПЕРЕД відправкою (dedup) ──────────────────────
        state["last"] = today
        save_json_file(MORNING_CTX_FILE, state)
        # GitHub dedup — стійкий до Railway restarts
        gh_morning["last"] = today
        _gh_save_json("monitor_morning_ctx.json", gh_morning, gh_morning_sha)

        send_telegram(msg)
        print(f"Morning context sent: shift={shift}, hour={h}")

    except Exception as e:
        print(f"check_morning_context error: {e}")


# ─── ТРЕКЕР БІГ / RUN COACH ──────────────────────────────────────────────────

RUN_COACH_FILE = os.path.join(_DATA_DIR, "monitor_run_coach.json")

def check_run_coach():
    """
    Тренер бігу — нагадує бігати 3 рази на тиждень.
    - Пн/Ср/Пт вихідного дня о 09:30: нагадування + план тренування
    - Якщо не бігав 3+ дні — нагадування будь-якого дня о 17:00
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")
    dow = now_local.weekday()  # 0=Пн

    state = load_json_file(RUN_COACH_FILE, default={})

    # Перевіримо скільки днів без бігу
    days_without = 0
    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from habits import load_data as _load_habits
        db = _load_habits()
        for i in range(1, 8):
            d = (now_local - timedelta(days=i)).strftime("%Y-%m-%d")
            if db.get(d, {}).get("run") is True:
                break
            days_without += 1
    except Exception:
        days_without = 0

    try:
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        today_shift = shift_info.get("today", "free")
    except Exception:
        today_shift = "free"

    is_free_day = today_shift == "free"

    # Нагадування тільки у вихідний день (не рання і не нічна зміна)
    # Час: якщо рання зміна сьогодні — не бігати (він на роботі)
    #       вихідний — нагадування після обіду о 13:00 якщо не бігав сьогодні
    #       або вранці о 09:30 у пн/ср/пт
    run_key_day = f"run_coach_{today}"

    if not is_free_day:
        return  # зміна — не чіпаємо

    # Варіант 1: вранці пн/ср/пт о 09:30
    if dow in (0, 2, 4) and h == 9 and 30 <= m < 35 and not state.get(run_key_day):
        plans = [
            "🏃 <b>День бігу!</b>\n\nПлан: 20-30 хв легкий біг.\n• Розминка 5 хв ходьба\n• Темп розмовний (можеш говорити)\n• Заминка 5 хв ходьба\n\n💪 Навіть 2 км — це прогрес!",
            "🏃 <b>Час бігти!</b>\n\nСьогодні: 25-35 хв.\n• Перші 10 хв повільно\n• Середина — комфортний темп\n• Останні 5 хв — трохи швидше\n\n🔥 Кожне тренування = -калорії = ближче до 78 кг!",
            "🏃 <b>Пробіжка!</b>\n\nЦього тижня скільки разів бігав? Якщо 0-1 — сьогодні обов'язково!\n• 20 хв — мінімум\n• Повітря + рух = настрій на весь день\n\n🎯 Ціль: 3 тренування/тиждень",
        ]
        send_telegram(plans[dow % 3])
        state[run_key_day] = True
        save_json_file(RUN_COACH_FILE, state)
        return

    # Варіант 2: якщо 3+ дні без бігу — нагадування після обіду о 13:00 у вихідний
    run_alert_key = f"run_alert_{today}"
    if days_without >= 3 and h == 13 and 0 <= m < 5 and not state.get(run_alert_key):
        send_telegram(
            f"🏃 <b>{days_without} днів без пробіжки!</b>\n\n"
            f"Сьогодні вихідний — гарний момент для 20 хв бігу після обіду!\n"
            f"Настрій гарантований 💪\n\n"
            f"<i>Ціль 78 кг — кожне тренування рахується!</i>"
        )
        state[run_alert_key] = True
        save_json_file(RUN_COACH_FILE, state)


# ─── НАГАДУВАННЯ ПРО ЇЖУ (дієтолог) ─────────────────────────────────────────

NUTRITION_FILE = os.path.join(_DATA_DIR, "monitor_nutrition.json")

def check_nutrition_reminder():
    """
    Дієтолог — нагадування про їжу з прив'язкою до графіку:
      Рання зміна:
        05:00 — сніданок перед виходом
        12:00 — обід на зміні
        19:00 — вечеря після зміни
      Нічна зміна:
        14:00 — основний прийом їжі перед зміною (головний!)
        21:00 — легкий перекус на зміні
      Вихідний:
        09:00 — сніданок
        13:00 — обід
        18:00 — вечеря (і нагадування про 16:8)
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    state = load_json_file(NUTRITION_FILE, default={})

    def already(key):
        return state.get(f"{today}_{key}")

    def mark(key):
        state[f"{today}_{key}"] = True
        save_json_file(NUTRITION_FILE, state)

    try:
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        shift = shift_info.get("today", "free")
    except Exception:
        shift = "free"

    # Рання зміна
    if shift == "early":
        if h == 5 and 0 <= m < 3 and not already("breakfast"):
            send_telegram(
                "🍳 <b>Сніданок!</b>\n\n"
                "Перед ранньою зміною важливо поїсти — дасть енергію на всі 12г.\n"
                "• Вівсянка / яйця / бутерброд\n"
                "• Вода або кава\n\n"
                "<i>Не виходь голодним!</i>"
            )
            mark("breakfast")
        elif h == 12 and 0 <= m < 3 and not already("lunch"):
            send_telegram(
                "🥗 <b>Обід на зміні!</b>\n\n"
                "Час поїсти — середина зміни.\n"
                "Намагайся уникати фастфуду:\n"
                "• Щось з собою > з кафетерію\n"
                "• Не забувай про воду 💧"
            )
            mark("lunch")
        elif h == 19 and 0 <= m < 3 and not already("dinner"):
            send_telegram(
                "🍽 <b>Вечеря!</b>\n\n"
                "Зміна позаду — час поїсти.\n"
                "💡 Порада: якщо практикуєш 16:8 —\n"
                "останній прийом їжі до 20:00\n\n"
                "<i>Легке і поживне 🥦</i>"
            )
            mark("dinner")

    # Нічна зміна
    elif shift == "night":
        if h == 14 and 0 <= m < 3 and not already("lunch"):
            send_telegram(
                "🍽 <b>Час обідати — перед нічною!</b>\n\n"
                "Це твій головний прийом їжі сьогодні.\n"
                "За 4г виходиш на зміну — поїж добре:\n"
                "• Білок + вуглеводи + овочі\n"
                "• Не переїдай — зміна ще попереду\n\n"
                "<i>Наступна нормальна їжа лише вранці!</i>"
            )
            mark("lunch")
        elif h == 21 and 0 <= m < 3 and not already("snack"):
            send_telegram(
                "🥜 <b>Перекус на зміні</b>\n\n"
                "Якщо голодний — час для легкого перекусу:\n"
                "• Горіхи, фрукт, йогурт\n"
                "• Уникай важкого — залишилась ще частина зміни\n\n"
                "<i>Тримай енергію, але не переїдай!</i>"
            )
            mark("snack")

    # Вихідний
    else:
        if h == 9 and 0 <= m < 3 and not already("breakfast"):
            send_telegram(
                "🌅 <b>Сніданок!</b>\n\n"
                "Починаємо день правильно 💪\n"
                "• Повноцінний сніданок = енергія на весь ранок\n"
                "• Не пропускай — особливо якщо плануєш біг!\n\n"
                "<i>Ціль 78 кг: важливо що і коли їсти</i>"
            )
            mark("breakfast")
        elif h == 13 and 0 <= m < 3 and not already("lunch"):
            send_telegram(
                "🥗 <b>Обід!</b>\n\n"
                "Час заправитись 🍽\n"
                "• Тарілка: ½ овочі, ¼ білок, ¼ крупи\n"
                "• Не переїдай — вечеря ще буде\n\n"
                "<i>Слідкуй за порціями → 78 кг реальні!</i>"
            )
            mark("lunch")
        elif h == 18 and 0 <= m < 3 and not already("dinner"):
            send_telegram(
                "🌙 <b>Вечеря!</b>\n\n"
                "Якщо практикуєш 16:8 — це останній прийом їжі.\n"
                "• Їж до 19:00\n"
                "• Легке: риба, овочі, яйця\n"
                "• Уникай солодкого та важкого\n\n"
                "💪 <i>Ціль 78 кг: дисципліна ввечері — результат вранці!</i>"
            )
            mark("dinner")


# ─── ЯКІСТЬ СНУ — РАНКОВЕ ПИТАННЯ ────────────────────────────────────────────

SLEEP_Q_FILE = os.path.join(_DATA_DIR, "monitor_sleep_q.json")

def check_sleep_quality():
    """
    Вранці питає про якість сну — адаптивний час:
      Після ранньої (о 18:30): як спалось перед зміною?
      Після нічної (о 07:00): як перенесли нічну?
      Вихідний (о 08:00): як спалось?
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    state = load_json_file(SLEEP_Q_FILE, default={})
    if state.get(f"asked_{today}"):
        return

    try:
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        shift = shift_info.get("today", "free")
        yesterday_shift = shift_info.get("tomorrow", "free")  # використаємо як proxy
    except Exception:
        shift = "free"

    trigger = None
    if shift == "free" and h == 8 and 0 <= m < 3:
        trigger = "free"
    elif shift == "night" and h == 7 and 0 <= m < 3:
        trigger = "night"

    if not trigger:
        return

    questions = {
        "free":  "😴 <b>Як спалось?</b>\n\nОціни якість сну минулої ночі:",
        "night": "😴 <b>Як перенесли нічну?</b>\n\nЯкість сну після зміни:"
    }

    try:
        import urllib.request as _ur
        tg_token = os.environ.get("TELEGRAM_TOKEN", "")
        tg_chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
        payload  = json.dumps({
            "chat_id": tg_chat,
            "text": questions[trigger],
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[
                {"text": "😩 Погано",    "callback_data": "sleep_q_1"},
                {"text": "😐 Нормально","callback_data": "sleep_q_2"},
                {"text": "😊 Добре",    "callback_data": "sleep_q_3"},
                {"text": "🌟 Відмінно", "callback_data": "sleep_q_4"},
            ]]}
        }).encode()
        req = _ur.Request(
            f"https://api.telegram.org/bot{tg_token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}
        )
        with _ur.urlopen(req, timeout=10) as r:
            pass
        state[f"asked_{today}"] = True
        save_json_file(SLEEP_Q_FILE, state)
        print("Sleep quality question sent")
    except Exception as e:
        print(f"sleep quality error: {e}")


# ─── КРИПТО РАНОК (щоденно при пробудженні) ──────────────────────────────────

CRYPTO_MORNING_FILE = os.path.join(_DATA_DIR, "monitor_crypto_morning.json")

def check_crypto_morning():
    """
    💹 CRYPTO DASHBOARD ЗРАНКУ — рання о 05:10, решта о 09:10.
    Ціни + міні-бар графік + Fear&Greed + AI сигнал.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    state = load_json_file(CRYPTO_MORNING_FILE, default={})
    if state.get("last") == today:
        return

    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        shift = shift_info.get("today", "free")
    except Exception:
        shift = "free"

    trigger = 5 if shift == "early" else 9
    if not (h == trigger and 10 <= m < 20):
        return

    try:
        coins_map = list(COINS.items())
        ids = ",".join(cg_id for _, cg_id in coins_map)
        url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}&price_change_percentage=24h,7d"
        req = urllib.request.Request(url, headers={"User-Agent": "bot"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = json.loads(r.read())
        data = {c["id"]: c for c in raw}

        lines_out = []
        lines_out.append(f"💹 <b>КРИПТО ДАШБОРД</b> · {today[5:]}")

        summary_parts = []
        for sym, cg_id in coins_map:
            c = data.get(cg_id, {})
            price = c.get("current_price")
            ch24  = c.get("price_change_percentage_24h") or 0
            ch7   = c.get("price_change_percentage_7d_in_currency") or 0
            if price is None: continue

            icon = "🟢" if ch24 > 0 else "🔴"
            sign24 = "+" if ch24 > 0 else ""
            sign7  = "+" if ch7  > 0 else ""

            # Бар від -5% до +5%
            bar_pos = int(max(0, min(10, (ch24 + 5) / 10 * 10)))
            bar = "🔴" * bar_pos + "⬜" * (10 - bar_pos) if ch24 < 0 else "🟢" * bar_pos + "⬜" * (10 - bar_pos)
            bar = bar[:10]

            lines_out.append(f"")
            lines_out.append(f"{icon} <b>{sym}</b>  <b>${price:,.2f}</b>")
            lines_out.append(f"   24г: {sign24}{ch24:.2f}%  7д: {sign7}{ch7:.1f}%")
            lines_out.append(f"   <code>[{bar}]</code>")

            summary_parts.append(f"{sym}{sign24}{ch24:.1f}%")

        # Fear & Greed
        try:
            fg_data = fetch_json("https://api.alternative.me/fng/?limit=1")
            if fg_data:
                fg_val = int(fg_data["data"][0]["value"])
                fg_label = fg_data["data"][0]["value_classification"]
                fg_bar_f = int(fg_val / 100 * 10)
                fg_bar = "🟢" * fg_bar_f + "⬜" * (10 - fg_bar_f)
                if fg_val <= 25: fg_ico = "😱"
                elif fg_val <= 45: fg_ico = "😟"
                elif fg_val <= 55: fg_ico = "😐"
                elif fg_val <= 75: fg_ico = "😊"
                else: fg_ico = "🤑"
                lines_out.append("")
                lines_out.append(f"{fg_ico} <b>Fear &amp; Greed:</b> {fg_val}/100 — {esc(fg_label)}")
                lines_out.append(f"   <code>{fg_bar}</code>")
        except Exception:
            pass

        # AI сигнал
        gemini_key = os.environ.get("GEMINI_API_KEY","")
        if gemini_key and summary_parts:
            try:
                prompt = (
                    f"Крипто зміни за 24г: {', '.join(summary_parts)}. "
                    f"Дай 1-2 речення аналіз для довгострокового HODLera: "
                    f"що це означає, чи варто щось робити? Без фінансових порад, просто аналіз."
                )
                payload = json.dumps({"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":600,"temperature":0.7}}).encode()
                req2 = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    data=payload, headers={"Content-Type":"application/json"}, method="POST"
                )
                with urllib.request.urlopen(req2, timeout=15) as r:
                    resp = json.loads(r.read())
                ai_signal = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
                lines_out.append("")
                lines_out.append(f"🤖 <i>{ai_signal}</i>")
            except Exception as e:
                print(f"crypto morning AI error: {e}")

        lines_out.append("")

        send_telegram("\n".join(lines_out))
        print(f"Crypto morning dashboard sent")
        state["last"] = today
        save_json_file(CRYPTO_MORNING_FILE, state)

    except Exception as e:
        print(f"check_crypto_morning error: {e}")


def check_week_goals():
    """
    Неділя о 20:30 — підсумок + цілі на наступний тиждень.
    AI аналізує тиждень і пропонує 3 конкретні цілі.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    dow = now_local.weekday()
    today = now_local.strftime("%Y-%m-%d")

    if not (dow == 6 and h == 20 and 30 <= m < 40):
        return

    state = load_json_file(WEEK_GOALS_FILE, default={})
    if state.get("last") == today:
        return

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        return

    try:
        # Збираємо дані за тиждень
        from habits import load_data as _load_habits
        db = _load_habits()
        week_days = [(now_local - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        habit_ids = ["run", "water", "tea", "shower"]
        habit_stats = {}
        for hid in habit_ids:
            done = sum(1 for d in week_days if db.get(d, {}).get(hid) is True)
            habit_stats[hid] = done

        # Вага
        try:
            from weight import load_weight_data
            wdata = load_weight_data()
            last_weight = None
            if wdata:
                last_key = sorted(wdata.keys())[-1]
                last_weight = wdata[last_key]["weight"]
        except Exception:
            last_weight = None

        prompt = (
            f"Тиждень Олега (Кошіце, Словаччина):\n"
            f"• Біг: {habit_stats.get('run',0)}/7 днів\n"
            f"• Вода: {habit_stats.get('water',0)}/7 днів\n"
            f"• Холодний душ: {habit_stats.get('shower',0)}/7 днів\n"
        )
        if last_weight:
            prompt += f"• Вага: {last_weight} кг (ціль 78 кг)\n"
        prompt += (
            f"\nСформулюй 3 конкретні цілі на наступний тиждень українською. "
            f"Враховуй слабкі місця цього тижня. Кожна ціль — одне речення, конкретна і досяжна. "
            f"Формат: '1. ... 2. ... 3. ...'"
        )

        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 800, "temperature": 0.8}
        }).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
            data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
        goals_text = resp["candidates"][0]["content"]["parts"][0]["text"].strip()

        # Підсумок тижня
        run_count = habit_stats.get("run", 0)
        run_emoji = "🏆" if run_count >= 3 else ("👍" if run_count >= 1 else "😔")

        msg = (
            f"📅 <b>Підсумок тижня</b>\n\n"
            f"{run_emoji} Біг: {run_count}/7 днів\n"
            f"💧 Вода: {habit_stats.get('water',0)}/7 днів\n"
            f"🚿 Душ: {habit_stats.get('shower',0)}/7 днів\n"
        )
        if last_weight:
            diff = round(last_weight - 78.0, 1)
            msg += f"⚖️ Вага: {last_weight} кг (до цілі: -{diff} кг)\n"

        msg += f"\n🎯 <b>Цілі на наступний тиждень:</b>\n{goals_text}"

        send_telegram(msg)
        print("Week goals sent")
        state["last"] = today
        save_json_file(WEEK_GOALS_FILE, state)

    except Exception as e:
        print(f"check_week_goals error: {e}")


# ─── СЛІДКУВАННЯ ЗА КАЛЕНДАРЕМ — ЩО ЗАРАЗ ВІДБУВАЄТЬСЯ ──────────────────────

CALENDAR_CONTEXT_FILE = os.path.join(_DATA_DIR, "monitor_calendar_context.json")

def check_calendar_live():
    """
    Відстежує поточні події в календарі — що відбувається прямо зараз.
    Кожні 5 хвилин перевіряє:
    - Якщо подія почалась — "🔔 Почалась: [назва]"
    - За 15 хв до події — "⏰ Через 15 хв: [назва]"
    - Нагадування про незаплановані вихідні (нічого в календарі — пропонує щось корисне)
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    # Тільки в активний час (07:00–23:00)
    if not (7 <= h <= 23):
        return

    token = _calendar_access_token()
    if not token:
        return

    # Стан зберігаємо в GitHub щоб не дублювати після редеплою
    try:
        import storage as _storage
        state = _storage.load("calendar_context.json", default={})
    except Exception:
        state = load_json_file(CALENDAR_CONTEXT_FILE, default={})

    def _save_state(s):
        try:
            import storage as _storage
            _storage.save("calendar_context.json", s)
        except Exception:
            save_json_file(CALENDAR_CONTEXT_FILE, s)

    try:
        token = _calendar_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        cal_id  = "novosadovoleg%40gmail.com"

        now_utc = datetime.now(timezone.utc)
        # Вікно: наступні 20 хвилин
        window_end = now_utc + timedelta(minutes=20)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(now_utc.isoformat())}"
            f"&timeMax={urllib.parse.quote(window_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=5"
        )
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            events = json.loads(r.read()).get("items", [])

        changed = False
        for ev in events:
            summary = ev.get("summary", "(без назви)")
            # Пропускаємо зміни та автоматичні події
            s_lower = summary.lower()
            if any(x in s_lower for x in ["зміна", "shift", "нагадування"]):
                continue

            start_str = ev["start"].get("dateTime") or ev["start"].get("date")
            try:
                dt_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                dt_local = dt_start + timedelta(hours=2)
                mins_until = int((dt_start - now_utc).total_seconds() / 60)
            except Exception:
                continue

            ev_key_15 = f"cal_15min_{ev['id']}_{dt_local.strftime('%Y%m%d%H%M')}"
            ev_key_now = f"cal_now_{ev['id']}_{dt_local.strftime('%Y%m%d%H%M')}"

            # За 15 хв
            if 12 <= mins_until <= 17 and not state.get(ev_key_15):
                send_telegram(
                    f"⏰ <b>Через 15 хв:</b> {esc(summary)}\n"
                    f"🕐 Початок о {dt_local.strftime('%H:%M')}"
                )
                state[ev_key_15] = True
                changed = True

            # Тільки що почалась (0–3 хв)
            elif 0 <= mins_until <= 3 and not state.get(ev_key_now):
                send_telegram(
                    f"🔔 <b>Починається зараз:</b> {esc(summary)}\n"
                    f"🕐 {dt_local.strftime('%H:%M')}"
                )
                state[ev_key_now] = True
                changed = True

        if changed:
            _save_state(state)

    except Exception as e:
        print(f"check_calendar_live error: {e}")

# ─── НАСТРІЙ ВЕЧОРА (21:30) ───────────────────────────────────────────────────

MOOD_FILE = os.path.join(_DATA_DIR, "monitor_mood.json")

def check_mood_evening():
    """
    😊 О 21:30 питає про настрій дня — 1-5 зірок.
    Зберігає для тижневого аналізу + AI реакція.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    if not (h == 21 and 30 <= m < 35):
        return

    state = load_json_file(MOOD_FILE, default={})
    if state.get(today):
        return

    # Відправляємо з inline кнопками через bot API
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        keyboard = {
            "inline_keyboard": [[
                {"text": "😩 1", "callback_data": "mood_1"},
                {"text": "😕 2", "callback_data": "mood_2"},
                {"text": "😐 3", "callback_data": "mood_3"},
                {"text": "😊 4", "callback_data": "mood_4"},
                {"text": "🤩 5", "callback_data": "mood_5"},
            ]]
        }
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT,
            "text": f"✨ <b>Як пройшов день?</b>\n\nОціни свій день від 1 до 5:",
            "parse_mode": "HTML",
            "reply_markup": keyboard
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        state[today] = "asked"
        save_json_file(MOOD_FILE, state)
        print("Mood question sent")
    except Exception as e:
        print(f"check_mood_evening error: {e}")


# ─── ПРОГРЕС КРОКІВ (18:00) ───────────────────────────────────────────────────

STEPS_FILE = os.path.join(_DATA_DIR, "monitor_steps.json")

def check_step_goal():
    """
    👟 О 18:00 у вільний день — перевіряє кроки з Health даних.
    Якщо < 8000 — мотивує дійти. Якщо > 10000 — хвалить.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    if not (h == 18 and 0 <= m < 5):
        return

    state = load_json_file(STEPS_FILE, default={})
    if state.get(today):
        return

    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        if shift_info.get("today") != "free":
            return  # На зміні — не турбуємо
    except Exception:
        pass

    try:
        from storage import load_health as _lh
        health = _lh()
        steps = health.get(today, {}).get("steps")

        if not steps:
            send_telegram(
                "👟 <b>Кроки сьогодні</b>\n\n"
                "Не бачу даних Apple Health 😅\n"
                "Скільки пройшов? Надішли /зд щоб записати!\n\n"
                "<i>Ціль: 10 000 кроків на день</i>"
            )
            state[today] = True
            save_json_file(STEPS_FILE, state)
            print("Step goal check sent: no data")
        else:
            step_goal = 10000
            remaining = step_goal - steps
            bar_f = min(10, int(steps / step_goal * 10))
            bar = "🟩" * bar_f + "⬜" * (10 - bar_f)
            pct = int(steps / step_goal * 100)

            if steps >= 12000:
                msg = (
                    f"👟 <b>Кроки: {steps:,}</b> 🏆\n"
                    f"<code>[{bar}]</code> {pct}%\n\n"
                    f"Фантастично! Перевиконав ціль на {steps-step_goal:,} кроків!\n"
                    f"<i>💪 Так тримати!</i>"
                )
            elif steps >= 10000:
                msg = (
                    f"👟 <b>Кроки: {steps:,}</b> ✅\n"
                    f"<code>[{bar}]</code> {pct}%\n\n"
                    f"Ціль 10 000 виконана! Гарна робота! 🎯"
                )
            elif steps >= 7000:
                msg = (
                    f"👟 <b>Кроки: {steps:,}</b> ⚡️\n"
                    f"<code>[{bar}]</code> {pct}%\n\n"
                    f"До цілі ще {remaining:,} кроків — 20 хв прогулянки вирішить справу!\n"
                    f"<i>Майже там!</i>"
                )
            else:
                msg = (
                    f"👟 <b>Кроки: {steps:,}</b> 📉\n"
                    f"<code>[{bar}]</code> {pct}%\n\n"
                    f"До цілі ще {remaining:,} кроків.\n"
                    f"Час невеличкої прогулянки? 🚶‍♂️\n"
                    f"<i>Кожен крок → ближче до 78 кг!</i>"
                )
            send_telegram(msg)
            state[today] = True
            save_json_file(STEPS_FILE, state)
            print(f"Step goal check sent: {steps}")

    except Exception as e:
        print(f"check_step_goal error: {e}")


# ─── П'ЯТНИЧНИЙ ПІДСУМОК ТИЖНЯ (20:00) ──────────────────────────────────────

FRIDAY_RECAP_FILE = os.path.join(_DATA_DIR, "monitor_friday_recap.json")

def check_friday_recap():
    """
    🎉 П'ятниця 20:00 — підсумок робочого тижня + AI мотивація на вихідні.
    Статистика змін, звички, вага.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    if not (now_local.weekday() == 4 and h == 20 and 0 <= m < 3):
        return

    state = load_json_file(FRIDAY_RECAP_FILE, default={})
    if state.get(today):
        return

    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from storage import load_habits as _lh, load_weight as _lw

        # Дні цього тижня (Пн–Пт)
        week_days = [(now_local - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4, -1, -1)]
        habits_db = _lh()
        wdata = _lw()

        HABITS = [("run","🏃","Біг"),("water","💧","Вода"),("shower","🚿","Душ")]
        habit_stats = {}
        for hid, hico, hname in HABITS:
            habit_stats[hid] = sum(1 for d in week_days if habits_db.get(d, {}).get(hid) is True)

        # Вага за тиждень
        w_start = w_end = None
        if wdata:
            w_week = {d: wdata[d] for d in week_days if d in wdata}
            if len(w_week) >= 2:
                sk = sorted(w_week.keys())
                w_start = w_week[sk[0]]
                w_end   = w_week[sk[-1]]

        lines_out = [
            f"🎉 <b>КІНЕЦЬ ТИЖНЯ — П'ятниця!</b>",
            f"",
        ]

        # Звички за тиждень
        lines_out.append("💪 <b>Звички за тиждень (Пн–Пт)</b>")
        for hid, hico, hname in HABITS:
            count = habit_stats[hid]
            dots = "🟩" * count + "⬜" * (5 - count)
            grade = "🏆" if count == 5 else ("⭐️" if count >= 3 else ("👍" if count >= 1 else "💤"))
            lines_out.append(f"   {hico} {hname}: {count}/5  {dots}  {grade}")
        lines_out.append("")

        # Вага
        if w_start and w_end:
            diff = round(w_end - w_start, 1)
            to_goal = round(w_end - 78.0, 1)
            trend = f"↗️ +{diff} кг" if diff > 0 else f"↘️ {diff} кг"
            lines_out.append(f"⚖️ <b>Вага:</b> {w_start}→{w_end} кг  {trend}")
            if to_goal > 0:
                lines_out.append(f"   🎯 До цілі 78 кг: ще -{to_goal} кг")
            else:
                lines_out.append("   🏆 ЦІЛЬ ДОСЯГНУТА!")
            lines_out.append("")

        # AI підсумок + план вихідних
        gemini_key = os.environ.get("GEMINI_API_KEY","")
        if gemini_key:
            try:
                run_c = habit_stats.get("run",0)
                w_info = f", вага {w_end} кг (ціль 78)" if w_end else ""
                prompt = (
                    f"Тиждень Олега: біг {run_c}/5 днів, вода {habit_stats.get('water',0)}/5{w_info}. "
                    f"Сьогодні п'ятниця. Дай: 1) одне речення підсумку тижня; "
                    f"2) одна конкретна пропозиція чим зайнятись на вихідних для здоров'я. "
                    f"Коротко, по-дружньому."
                )
                payload = json.dumps({"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":600,"temperature":0.8}}).encode()
                req = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    data=payload, headers={"Content-Type":"application/json"}, method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    resp = json.loads(r.read())
                ai_text = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
                lines_out.append(f"🤖 <i>{ai_text}</i>")
                lines_out.append("")
            except Exception as e:
                print(f"friday recap AI error: {e}")

        lines_out.append("🎊 Хороших вихідних, Олеже!")

        send_telegram("\n".join(lines_out))
        state[today] = True
        save_json_file(FRIDAY_RECAP_FILE, state)
        print("Friday recap sent")

    except Exception as e:
        print(f"check_friday_recap error: {e}")


# ─── ТРЕНД ВАГИ — АЛЕРТ ЯКЩО РОСТЕ 3 ДНІ ПОСПІЛЬ ────────────────────────────

WEIGHT_TREND_FILE = os.path.join(_DATA_DIR, "monitor_weight_trend.json")

def check_weight_trend_alert():
    """
    ⚠️ Якщо вага росте 3+ дні поспіль — проактивний алерт о 10:00.
    Мотивує скоригувати харчування/активність.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    if not (h == 10 and 0 <= m < 5):
        return

    state = load_json_file(WEIGHT_TREND_FILE, default={})
    week_key = now_local.strftime("%Y-W%W")
    if state.get(week_key):
        return

    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from storage import load_weight as _lw
        wdata = _lw()
        if not wdata or len(wdata) < 3:
            return

        sorted_keys = sorted(wdata.keys())[-5:]
        w_vals = [wdata[k] for k in sorted_keys]

        # Перевіряємо: 3+ дні росту поспіль
        rising_days = 0
        for i in range(len(w_vals) - 1, 0, -1):
            if w_vals[i] > w_vals[i-1]:
                rising_days += 1
            else:
                break

        if rising_days < 3:
            return

        # Графік останніх 5 вимірювань
        w_min = min(w_vals) - 0.3
        w_max = max(w_vals) + 0.3
        blocks = ["⬜","🟦","🟦","🟩","🟩","🟨","🟧","🟥"]
        bars = []
        for v in w_vals:
            b = int((v - w_min) / max(w_max - w_min, 0.1) * 7)
            bars.append(blocks[max(0, min(7, b))])

        total_rise = round(w_vals[-1] - w_vals[-rising_days-1], 1)
        to_goal = round(w_vals[-1] - 78.0, 1)

        send_telegram(
            f"⚠️ <b>Вага зростає {rising_days} дні поспіль!</b>\n"
            f"<code>{''.join(bars)}</code>  ↗️ +{total_rise} кг\n"
            f"Зараз: <b>{w_vals[-1]} кг</b>  |  До 78 кг: -{to_goal}\n\n"
            f"🔍 Можливі причини:\n"
            f"• 💧 Недостатньо води\n"
            f"• 🍽 Пізня їжа або великі порції\n"
            f"• 🏃 Мало руху\n\n"
            f"<i>Маленькі корекції → великі результати!</i>"
        )
        state[week_key] = True
        save_json_file(WEIGHT_TREND_FILE, state)
        print(f"Weight trend alert: {rising_days} days rising")

    except Exception as e:
        print(f"check_weight_trend_alert error: {e}")


# ─── ТРАНЗИТ ПЛАНЕТ — ЗМІНА ЗНАКУ АБО ДОМУ ───────────────────────────────────

PLANET_INGRESS_FILE = os.path.join(_DATA_DIR, "monitor_planet_ingress.json")

def check_planet_ingress():
    """
    Відстежує коли транзитна планета переходить в інший знак або натальний дім.
    Шле сповіщення при кожному переході. Стан зберігається в GitHub.
    Перевірка кожні 30 хв (для Місяця достатньо, він рухається ~1° за 2г).
    """
    try:
        from kerykeion import AstrologicalSubject as _AS
        from astro import (
            PLANETS_LIST, CURRENT_LAT, CURRENT_LON,
            BIRTH_YEAR, BIRTH_MONTH, BIRTH_DAY, BIRTH_HOUR, BIRTH_MIN,
            BIRTH_LAT, BIRTH_LON, BIRTH_TZ,
            _get_natal_house, _sign_ua, MOON_SIGN_TIPS
        )
    except ImportError as e:
        print(f"check_planet_ingress import error: {e}")
        return

    now_utc   = datetime.now(timezone.utc)
    now_local = now_utc + timedelta(hours=2)

    try:
        transit = _AS(
            "t",
            now_utc.year, now_utc.month, now_utc.day,
            now_utc.hour, now_utc.minute,
            lat=CURRENT_LAT, lng=CURRENT_LON,
            tz_str="UTC", zodiac_type="Tropic",
            houses_system_identifier="P", online=False,
        )
        natal = _AS(
            "n",
            BIRTH_YEAR, BIRTH_MONTH, BIRTH_DAY, BIRTH_HOUR, BIRTH_MIN,
            lat=BIRTH_LAT, lng=BIRTH_LON,
            tz_str=BIRTH_TZ, zodiac_type="Tropic",
            houses_system_identifier="P", online=False,
        )
    except Exception as e:
        print(f"check_planet_ingress kerykeion error: {e}")
        return

    _HOUSE_NAMES = [
        "first","second","third","fourth","fifth","sixth",
        "seventh","eighth","ninth","tenth","eleventh","twelfth"
    ]
    natal_cusps = [getattr(natal, f"{h}_house").abs_pos for h in _HOUSE_NAMES]

    # Поточний стан всіх планет
    current = {}
    for key, name_ua in PLANETS_LIST:
        p = getattr(transit, key, None)
        if not p:
            continue
        house = _get_natal_house(p.abs_pos, natal_cusps)
        sign  = _sign_ua(p.sign)
        current[key] = {"name": name_ua, "sign": sign, "house": house, "retro": p.retrograde}

    # Завантажуємо попередній стан
    prev = load_json_file(PLANET_INGRESS_FILE, default={})

    HOUSE_MEANING = {
        1: "🏃 Особистість, зовнішність",
        2: "💰 Фінанси, цінності",
        3: "💬 Комунікація, поїздки",
        4: "🏠 Дім, родина",
        5: "🎭 Творчість, роман, діти",
        6: "⚕️ Здоров'я, робота, рутина",
        7: "🤝 Партнерства, відносини",
        8: "🔮 Трансформація, ресурси партнера",
        9: "🌍 Подорожі, навчання, філософія",
        10: "🏆 Кар'єра, репутація",
        11: "👥 Друзі, цілі, спільноти",
        12: "🌊 Підсвідоме, усамітнення",
    }

    alerts = []
    changed = False

    for key, cur in current.items():
        old = prev.get(key, {})
        name = cur["name"]
        sign = cur["sign"]
        house = cur["house"]
        retro = cur["retro"]
        retro_str = " <b>℞</b>" if retro else ""

        # Зміна знаку
        if old.get("sign") and old["sign"] != sign:
            tip = MOON_SIGN_TIPS.get(sign, "")
            msg = (
                f"🌀 <b>{name}{retro_str} увійшов у {sign}</b>\n"
                f"🏠 Натальний дім {house} — {HOUSE_MEANING.get(house, '')}\n"
            )
            if tip:
                msg += f"<i>{tip}</i>"
            alerts.append(msg)
            print(f"Planet ingress (sign): {name} → {sign}")

        # Зміна натального дому
        elif old.get("house") and old["house"] != house:
            msg = (
                f"🚪 <b>{name}{retro_str} перейшов у {house}-й натальний дім</b>\n"
                f"♑ Знак: {sign}\n"
                f"{HOUSE_MEANING.get(house, '')}"
            )
            alerts.append(msg)
            print(f"Planet ingress (house): {name} → house {house}")

        changed = True

    # Зберігаємо поточний стан
    save_json_file(PLANET_INGRESS_FILE, current)

    for msg in alerts:
        send_telegram(msg)

# ─── Транзитні аспекти до натальних планет ────────────────────────────────────
TRANSIT_ASPECTS_FILE = os.path.join(_DATA_DIR, "monitor_transit_aspects.json")

NATAL_PLANETS_DATA = {
    # key: (lon, sign_ua, name_ua)  — заповнюється динамічно з kerykeion
}

ASP_EXACT_DEF = {
    0:   ("☌", "Кон'юнкція",   "#E53935"),  # червоний
    60:  ("⚹", "Секстиль",     "#43A047"),  # зелений
    90:  ("□", "Квадратура",   "#FF9800"),  # помаранчевий
    120: ("△", "Трин",         "#1565C0"),  # синій
    150: ("⚻", "Квінкункс",    "#9C27B0"),  # фіолетовий
    180: ("☍", "Опозиція",     "#E53935"),  # червоний
}

ASP_HOUSE_MEANING = {
    1:"Особистість",2:"Фінанси",3:"Комунікація",4:"Дім/Родина",
    5:"Творчість",6:"Здоров'я",7:"Партнерства",8:"Трансформація",
    9:"Подорожі",10:"Кар'єра",11:"Друзі/Цілі",12:"Підсвідоме",
}

ASP_PLANET_MEANING = {
    "sun":     "💫 Воля, его, батько",
    "moon":    "🌙 Емоції, мати, звички",
    "mercury": "☿ Розум, комунікація",
    "venus":   "♀ Кохання, краса, гроші",
    "mars":    "♂ Енергія, дія, конфлікт",
    "jupiter": "♃ Удача, розширення",
    "saturn":  "♄ Обмеження, дисципліна",
    "uranus":  "⛢ Зміни, несподіванки",
    "neptune": "♆ Ілюзії, духовність",
    "pluto":   "♇ Трансформація, влада",
}

_TRANSIT_PLANETS_ORDER = ['sun','moon','mercury','venus','mars','jupiter','saturn','uranus','neptune','pluto']

def check_transit_aspects():
    """
    Кожні 30 хв перевіряє чи транзитна планета ЩОЙНО увійшла в орб точного аспекту
    до натальної планети (орб ≤ 1.5°) або виходить з нього.
    Надсилає сповіщення з AI-поясненням.
    """
    try:
        from kerykeion import AstrologicalSubject as _AS
        from astro import (
            CURRENT_LAT, CURRENT_LON,
            BIRTH_YEAR, BIRTH_MONTH, BIRTH_DAY, BIRTH_HOUR, BIRTH_MIN,
            BIRTH_LAT, BIRTH_LON, BIRTH_TZ,
            _sign_ua
        )
    except ImportError as e:
        print(f"check_transit_aspects import error: {e}")
        return

    now_utc = datetime.now(timezone.utc)

    try:
        transit_subj = _AS(
            "tr",
            now_utc.year, now_utc.month, now_utc.day,
            now_utc.hour, now_utc.minute,
            lat=CURRENT_LAT, lng=CURRENT_LON,
            tz_str="UTC", zodiac_type="Tropic",
            houses_system_identifier="P", online=False,
        )
        natal_subj = _AS(
            "nt",
            BIRTH_YEAR, BIRTH_MONTH, BIRTH_DAY, BIRTH_HOUR, BIRTH_MIN,
            lat=BIRTH_LAT, lng=BIRTH_LON,
            tz_str=BIRTH_TZ, zodiac_type="Tropic",
            houses_system_identifier="P", online=False,
        )
    except Exception as e:
        print(f"check_transit_aspects kerykeion error: {e}")
        return

    _HOUSE_NAMES_TA = ["first","second","third","fourth","fifth","sixth",
                       "seventh","eighth","ninth","tenth","eleventh","twelfth"]
    natal_cusps = [getattr(natal_subj, f"{h}_house").abs_pos for h in _HOUSE_NAMES_TA]

    def _get_house_ta(lon):
        for i in range(12):
            start = natal_cusps[i]
            end   = natal_cusps[(i+1) % 12]
            if start < end:
                if start <= lon < end: return i+1
            else:
                if lon >= start or lon < end: return i+1
        return 1

    # Поточні позиції транзитних планет
    transit_positions = {}
    for key in _TRANSIT_PLANETS_ORDER:
        p = getattr(transit_subj, key, None)
        if p:
            transit_positions[key] = {
                "lon": p.abs_pos,
                "sign": _sign_ua(p.sign),
                "retro": p.retrograde,
            }

    # Натальні позиції (фіксовані)
    natal_positions = {}
    for key in _TRANSIT_PLANETS_ORDER:
        p = getattr(natal_subj, key, None)
        if p:
            natal_positions[key] = {
                "lon": p.abs_pos,
                "sign": _sign_ua(p.sign),
                "house": _get_house_ta(p.abs_pos),
            }

    # Завантажуємо попередній стан активних аспектів
    # Формат: {"transit_key__natal_key__asp_deg": {"orb": 1.2, "sent": true}}
    prev_state = load_json_file(TRANSIT_ASPECTS_FILE, default={})
    new_state  = {}
    alerts = []

    ORB_ENTER = 1.5   # увійти в орб при ≤ цьому
    ORB_EXACT = 0.5   # "точний" аспект

    PLANET_SYMBOLS_TA = {
        'sun':'☉','moon':'☽','mercury':'☿','venus':'♀','mars':'♂',
        'jupiter':'♃','saturn':'♄','uranus':'⛢','neptune':'♆','pluto':'♇',
    }
    PLANET_NAMES_UA = {
        'sun':'Сонце','moon':'Місяць','mercury':'Меркурій','venus':'Венера',
        'mars':'Марс','jupiter':'Юпітер','saturn':'Сатурн',
        'uranus':'Уран','neptune':'Нептун','pluto':'Плутон',
    }

    for tr_key, tr_data in transit_positions.items():
        for nt_key, nt_data in natal_positions.items():
            tr_lon = tr_data["lon"]
            nt_lon = nt_data["lon"]

            diff = abs(tr_lon - nt_lon) % 360
            if diff > 180:
                diff = 360 - diff

            for asp_deg, (asp_sym, asp_name_ua, asp_color) in ASP_EXACT_DEF.items():
                orb = abs(diff - asp_deg)
                state_key = f"{tr_key}__{nt_key}__{asp_deg}"

                if orb <= ORB_ENTER:
                    new_state[state_key] = {"orb": round(orb, 2), "sent": False}

                    was_active = state_key in prev_state
                    was_sent   = prev_state.get(state_key, {}).get("sent", False)
                    prev_orb   = prev_state.get(state_key, {}).get("orb", 999)

                    # Надсилаємо якщо:
                    # 1) Щойно увійшов в орб (не був раніше)
                    # 2) Або став точнішим (зменшився orb і ще не надсилали "exact")
                    should_send = False
                    is_exact    = orb <= ORB_EXACT

                    if not was_active:
                        should_send = True
                        new_state[state_key]["event"] = "enter"
                    elif is_exact and not prev_state.get(state_key, {}).get("exact_sent", False):
                        should_send = True
                        new_state[state_key]["event"] = "exact"
                        new_state[state_key]["exact_sent"] = True
                    else:
                        # Зберігаємо попередній стан exact_sent
                        new_state[state_key]["exact_sent"] = prev_state.get(state_key, {}).get("exact_sent", False)

                    if should_send:
                        tr_name  = PLANET_NAMES_UA.get(tr_key, tr_key)
                        nt_name  = PLANET_NAMES_UA.get(nt_key, nt_key)
                        tr_sym   = PLANET_SYMBOLS_TA.get(tr_key,'?')
                        nt_sym   = PLANET_SYMBOLS_TA.get(nt_key,'?')
                        nt_house = nt_data["house"]
                        nt_sign  = nt_data["sign"]
                        tr_sign  = tr_data["sign"]
                        retro_s  = " ℞" if tr_data["retro"] else ""
                        exact_s  = " <b>(ТОЧНИЙ!)</b>" if is_exact else f" (орб {orb:.1f}°)"

                        # AI-пояснення аспекту
                        situation = (
                            f"Транзитний {tr_name}{retro_s} у {tr_sign} формує {asp_name_ua} "
                            f"({asp_sym}{exact_s}) до натального {nt_name} у {nt_sign} ({nt_house}-й дім). "
                            f"Натальний {nt_name}: {ASP_PLANET_MEANING.get(nt_key,'')}. "
                            f"Дім {nt_house}: {ASP_HOUSE_MEANING.get(nt_house,'')}."
                        )
                        ai_text = _ai_personal_message(
                            situation=situation,
                            max_tokens=150
                        )

                        event_label = "🎯 ТОЧНИЙ АСПЕКТ" if is_exact else "🔭 Новий транзит"
                        msg = (
                            f"{event_label}\n"
                            f"{tr_sym} <b>{tr_name}</b>{retro_s} {asp_sym} {nt_sym} <b>Натальний {nt_name}</b>{exact_s}\n"
                            f"📍 {tr_name} у <b>{tr_sign}</b> | Натальний {nt_name} у {nt_sign}, {nt_house}-й дім\n"
                            f"<i>{asp_name_ua} · {ASP_HOUSE_MEANING.get(nt_house,'')}</i>\n\n"
                            f"{ai_text}"
                        )
                        alerts.append(msg)
                        new_state[state_key]["sent"] = True

                # Якщо вийшов з орбу — видаляємо з стану (просто не додаємо в new_state)

    # Зберігаємо новий стан
    save_json_file(TRANSIT_ASPECTS_FILE, new_state)

    for msg in alerts:
        send_telegram(msg)
        import time as _time_ta
        _time_ta.sleep(2)  # пауза між повідомленнями


# ─── НАГАДУВАННЯ ПРО ВАЖЛИВІ ЛИСТИ БЕЗ ВІДПОВІДІ ────────────────────────────

def check_important_emails_followup():
    """Щогодини (08:00-22:00): нагадує про важливі листи без відповіді > 24г."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (8 <= now_local.hour < 22):
        return

    import base64 as _b64i, urllib.request as _uri
    gh_url = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/important_emails.json"
    gh_headers = {
        "Authorization": f"token {os.environ.get('GITHUB_TOKEN','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')}",
        "User-Agent": "bot"
    }

    try:
        req = _uri.Request(gh_url, headers=gh_headers)
        with _uri.urlopen(req, timeout=10) as r:
            gh_data = json.loads(r.read())
            emails = json.loads(_b64i.b64decode(gh_data["content"]).decode())
            sha = gh_data["sha"]
    except Exception:
        return  # файл не існує або порожній

    if not emails:
        return

    now_utc = datetime.now(timezone.utc)
    still_pending = []
    reminders = []

    for em in emails:
        saved_at_str = em.get("saved_at", "")
        try:
            saved_at = datetime.fromisoformat(saved_at_str).replace(tzinfo=timezone.utc)
        except Exception:
            still_pending.append(em)
            continue

        age_hours = (now_utc - saved_at).total_seconds() / 3600
        reminded = em.get("reminded", False)

        if age_hours >= 24 and not reminded:
            reminders.append(em)
            em["reminded"] = True

        still_pending.append(em)

    if reminders:
        for em in reminders:
            msg = (
                f"⭐ <b>Нагадування: важливий лист без відповіді</b>\n\n"
                f"👤 <b>Від:</b> {esc(em.get('sender','')[:60])}\n"
                f"📋 <b>Тема:</b> {esc(em.get('subject','')[:70])}\n"
                f"<i>{esc(em.get('preview','')[:200])}</i>\n\n"
                f"⏰ Збережено більше 24 годин тому. Не забув відповісти?"
            )
            send_telegram(msg)

        # Оновлюємо файл
        content = _b64i.b64encode(json.dumps(still_pending, ensure_ascii=False, indent=2).encode()).decode()
        body_gh = json.dumps({"message": "followup update", "content": content, "sha": sha}).encode()
        req2 = _uri.Request(gh_url, data=body_gh, headers={**gh_headers, "Content-Type": "application/json"}, method="PUT")
        try:
            _uri.urlopen(req2, timeout=15)
        except Exception as e:
            print(f"important_emails save error: {e}")


# ─── НАГАДУВАННЯ -24г ДО ДЕДЛАЙНІВ З ЛИСТІВ ─────────────────────────────────

def check_email_deadlines():
    """
    Щоранку о 09:05 — перевіряє email_deadlines.json на GitHub.
    Якщо є подія завтра (або сьогодні) — надсилає нагадування.
    """
    import base64 as _b64ed
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    if not (h == 9 and 5 <= m < 10):
        return

    today     = now_local.strftime("%Y-%m-%d")
    tomorrow  = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")

    state_key = f"email_dl_{today}"
    state = load_json_file(os.path.join(_DATA_DIR, "monitor_email_dl.json"), default={})
    if state.get(state_key):
        return

    try:
        gh_token = os.environ.get("GITHUB_TOKEN", "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")
        gh_url   = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/email_deadlines.json"
        gh_hdrs  = {"Authorization": f"token {gh_token}", "User-Agent": "monitor"}

        req = urllib.request.Request(gh_url, headers=gh_hdrs)
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = json.loads(r.read())
        dl_sha  = raw.get("sha", "")
        dl_list = json.loads(_b64ed.b64decode(raw["content"]).decode())
        if not isinstance(dl_list, list):
            return

        alerts = []
        updated = False
        for item in dl_list:
            if item.get("reminded"):
                continue
            ev_date = item.get("date", "")
            if ev_date in (today, tomorrow):
                label = "📌 СЬОГОДНІ" if ev_date == today else "📅 ЗАВТРА"
                alerts.append(
                    f"{label}: <b>{item.get('title','')}</b>\n"
                    f"   📧 З листа: <i>{item.get('subject','')[:60]}</i>"
                )
                item["reminded"] = True
                updated = True

        if alerts:
            msg = "⏰ <b>Дедлайни з листів:</b>\n\n" + "\n\n".join(alerts)
            send_telegram(msg)
            print(f"email_deadlines: {len(alerts)} alerts sent")

        # Зберігаємо оновлений список назад
        if updated:
            content_enc = _b64ed.b64encode(
                json.dumps(dl_list, ensure_ascii=False, indent=2).encode()
            ).decode()
            body_put = json.dumps({
                "message": "deadlines reminded",
                "content": content_enc,
                "sha": dl_sha
            }).encode()
            req2 = urllib.request.Request(
                gh_url, data=body_put,
                headers={**gh_hdrs, "Content-Type": "application/json"},
                method="PUT"
            )
            urllib.request.urlopen(req2, timeout=15)

        state[state_key] = True
        save_json_file(os.path.join(_DATA_DIR, "monitor_email_dl.json"), state)

    except Exception as e:
        print(f"check_email_deadlines error: {e}")


def check_shopping_reminder():
    """
    Нагадування про список покупок о 12:45 і 19:15 (Košice UTC+2).
    Надсилає тільки якщо є незавершені пункти.
    """
    try:
        from datetime import datetime, timezone, timedelta
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        now_local = datetime.now(timezone.utc) + timedelta(hours=2)
        h, m = now_local.hour, now_local.minute

        # Вікна: 12:44–12:46 і 19:14–19:16
        is_noon    = (h == 12 and 44 <= m <= 46)
        is_evening = (h == 19 and 14 <= m <= 16)

        if not (is_noon or is_evening):
            return

        # Захист від дублів
        date_str = now_local.strftime("%Y-%m-%d")
        slot = "noon" if is_noon else "evening"
        state_key = f"shopping_reminded_{date_str}_{slot}"

        state = load_json_file(os.path.join(_DATA_DIR, "monitor_shopping_state.json"), default={})
        if state.get(state_key):
            return

        # Перевіряємо список
        import shopping as _sh
        items = _sh.get_items()
        uncompleted = [i for i in items if not i["done"]]

        if not uncompleted:
            return

        text_list = "\n".join(f"⬜ {i['text']}" for i in uncompleted)
        time_label = "обід" if is_noon else "вечір"
        msg = (
            f"🛒 <b>Список покупок</b> — нагадування ({time_label})\n\n"
            f"{text_list}\n\n"
            f"Є {len(uncompleted)} пункт(ів) не куплено."
        )

        kb = {"inline_keyboard": [
            [{"text": "✅ Все куплено", "callback_data": "shopping_all_done"},
             {"text": "📝 Відмітити", "callback_data": "shopping_mark"}]
        ]}

        send_message(msg, reply_markup=kb)

        state[state_key] = True
        save_json_file(os.path.join(_DATA_DIR, "monitor_shopping_state.json"), state)
        print(f"check_shopping_reminder: sent ({slot}), {len(uncompleted)} items")

    except Exception as e:
        print(f"check_shopping_reminder error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# ▌ НОВІ ФУНКЦІЇ — АПГРЕЙД БОТА
# ═══════════════════════════════════════════════════════════════════════════════

# ─── 1. STRAVA WATCHER — авто-сповіщення після тренування ────────────────────

_STRAVA_LAST_ACT_FILE = os.path.join(_DATA_DIR, "monitor_strava_last_activity.json")

def check_strava_new_activity():
    """
    Перевіряє кожні 10 хв: чи є нова активність у Strava.
    Якщо є — надсилає результат + AI аналіз темпу і порівняння з попереднім.
    """
    try:
        import sys as _sys_s
        _sys_s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from strava import _get_access_token

        state = load_json_file(_STRAVA_LAST_ACT_FILE, default={})
        last_id = state.get("last_id")

        token = _get_access_token()
        import requests as _req_s
        r = _req_s.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 1, "page": 1},
            timeout=15
        )
        r.raise_for_status()
        acts = r.json()
        if not acts:
            return

        a = acts[0]
        act_id = str(a["id"])

        if act_id == str(last_id):
            return  # не нова

        # Перевірити що активність повністю синхронізована (не 0 даних)
        dist_km_raw = round(a.get("distance", 0) / 1000, 2)
        dur_sec_raw = a.get("moving_time", 0)
        if dist_km_raw < 0.3 or dur_sec_raw < 60:
            # Активність ще синхронізується — НЕ зберігаємо last_id
            # Щоб перевірити знову наступного разу
            print(f"[Strava] Activity {act_id} incomplete (dist={dist_km_raw}km, dur={dur_sec_raw}s) — skipping, will retry")
            return

        # Нова повна активність!
        state["last_id"] = act_id
        save_json_file(_STRAVA_LAST_ACT_FILE, state)

        dist_km  = dist_km_raw
        dur_sec  = dur_sec_raw
        dur_min  = dur_sec // 60
        act_type = a.get("type", "Run")
        name     = a.get("name", "Тренування")
        elev     = a.get("total_elevation_gain", 0)
        hr       = a.get("average_heartrate")
        kudos    = a.get("kudos_count", 0)
        calories = a.get("calories") or a.get("kilojoules", 0)

        type_emoji = {"Run": "🏃", "TrailRun": "🏔", "VirtualRun": "💻",
                      "Ride": "🚴", "Swim": "🏊", "Walk": "🚶"}.get(act_type, "🏃")

        # Темп
        pace_str = "—"
        if dist_km > 0:
            pace_sec_per_km = dur_sec / dist_km
            pace_str = f"{int(pace_sec_per_km//60)}:{int(pace_sec_per_km%60):02d} хв/км"

        lines = [
            f"{type_emoji} <b>Нова активність!</b>",
            f"",
            f"🏷 <b>{name}</b>",
            f"📏 Дистанція:  <b>{dist_km} км</b>",
            f"⏱ Час:         <b>{dur_min} хв</b>",
            f"⚡️ Темп:        <b>{pace_str}</b>",
        ]
        if elev:
            lines.append(f"⛰ Набір:       <b>{elev:.0f} м</b>")
        if hr:
            lines.append(f"❤️ ЧСС:        <b>{hr:.0f} уд/хв</b>")
        if calories:
            lines.append(f"🔥 Калорії:    <b>{int(calories)} ккал</b>")

        # Тижнева статистика
        try:
            from strava import get_week_stats
            wk = get_week_stats()
            if wk:
                goal_km = 40
                pct = min(wk["km"] / goal_km, 1.0)
                filled = int(pct * 10)
                bar = "█" * filled + "░" * (10 - filled)
                lines.append(f"")
                lines.append(f"📅 <b>Тиждень:</b> {wk['runs']} пробіжок · {wk['km']} км")
                lines.append(f"[{bar}] {wk['km']}/{goal_km} км")
        except Exception:
            pass

        # AI коментар
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key and dist_km > 0:
            try:
                import json as _json_s
                import urllib.request as _ur_s
                prompt = (
                    f"Я пробіг {dist_km} км за {dur_min} хв, темп {pace_str}."
                    + (f" ЧСС {hr:.0f} уд/хв." if hr else "")
                    + (f" Набір висоти {elev:.0f} м." if elev else "")
                    + " Дай коротку (2-3 речення) мотивуючу оцінку тренування українською. "
                    + "Будь конкретним: добре чи треба покращити темп, чи норм для відновлення? Без зайвих слів."
                )
                payload = _json_s.dumps({
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 600, "temperature": 0.7}
                }).encode()
                req_ai = _ur_s.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}",
                    data=payload, headers={"Content-Type": "application/json"}
                )
                with _ur_s.urlopen(req_ai, timeout=10) as _resp_ai:
                    ai_data = _json_s.loads(_resp_ai.read())
                ai_comment = ai_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                lines.append(f"\n💬 <i>{ai_comment}</i>")
            except Exception as _ai_e:
                print(f"strava AI comment error: {_ai_e}")

        send_telegram("\n".join(lines))
        print(f"[Strava] New activity sent: {act_id} — {dist_km}km")

    except Exception as e:
        print(f"check_strava_new_activity error: {e}")



def check_strava_weekly_report():
    """
    Тижневий звіт бігу — відправляється по неділях в 20:00.
    Повний аналіз + графік за останні 8 тижнів.
    """
    try:
        import sys as _sys_sr
        _sys_sr.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from strava import format_weekly_run_report
        from strava_charts import plot_week_chart

        text = format_weekly_run_report()
        send_telegram(text)

        # Графік
        chart_bytes = plot_week_chart(weeks_back=8)
        if chart_bytes:
            _send_photo_bytes(chart_bytes, caption="📊 Прогрес по тижнях")

        print("[Strava] Weekly run report sent")
    except Exception as e:
        print(f"check_strava_weekly_report error: {e}")


def check_strava_monthly_report():
    """
    Місячний звіт бігу — відправляється 1-го числа о 09:00.
    Повний аналіз + місячний графік + річний графік.
    """
    try:
        import sys as _sys_mr
        _sys_mr.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from strava import format_monthly_run_report
        from strava_charts import plot_month_chart, plot_year_chart
        from datetime import datetime as _dt_mr
        now = _dt_mr.now()
        # Звітуємо за попередній місяць
        if now.month == 1:
            report_year, report_month = now.year - 1, 12
        else:
            report_year, report_month = now.year, now.month - 1

        text = format_monthly_run_report(report_year, report_month)
        send_telegram(text)

        # Місячний графік
        month_chart = plot_month_chart(report_year, report_month)
        if month_chart:
            import calendar as _cal_mr
            mnames = ["","Січень","Лютий","Березень","Квітень","Травень","Червень",
                      "Липень","Серпень","Вересень","Жовтень","Листопад","Грудень"]
            _send_photo_bytes(month_chart, caption=f"📊 {mnames[report_month]} — по днях")

        # Річний графік
        year_chart = plot_year_chart(now.year)
        if year_chart:
            _send_photo_bytes(year_chart, caption=f"📊 {now.year} рік — по місяцях")

        print("[Strava] Monthly run report sent")
    except Exception as e:
        print(f"check_strava_monthly_report error: {e}")


def send_strava_chart_daily():
    """
    Відправляє графік 2 рази на день (вранці та ввечері).
    Ранок — місячний, вечір — тижневий прогрес.
    """
    try:
        import sys as _sys_sc
        _sys_sc.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from strava_charts import plot_month_chart, plot_week_chart
        from datetime import datetime as _dt_sc
        now = _dt_sc.now()

        if now.hour < 14:
            # Ранок — місячний
            chart = plot_month_chart()
            caption = f"📊 Біг — {now.strftime('%B %Y')}"
        else:
            # Вечір — тижневий
            chart = plot_week_chart(weeks_back=8)
            caption = "📊 Прогрес по тижнях"

        if chart:
            _send_photo_bytes(chart, caption=caption)

        print(f"[Strava] Chart sent ({now.strftime('%H:%M')})")
    except Exception as e:
        print(f"send_strava_chart_daily error: {e}")

# ─── 2. КУРС ВАЛЮТ — в кожен звіт ────────────────────────────────────────────

def get_currency_rates() -> str:
    """Живі курси EUR/USD/CZK/PLN від exchangerate-api (безкоштовно)."""
    try:
        import urllib.request as _ur_c
        import json as _json_c

        # Безкоштовний endpoint — не потребує ключа
        url = "https://open.er-api.com/v6/latest/EUR"
        with _ur_c.urlopen(url, timeout=8) as _r:
            data = _json_c.loads(_r.read())

        if data.get("result") != "success":
            return ""

        rates = data.get("rates", {})
        usd = rates.get("USD")
        czk = rates.get("CZK")
        pln = rates.get("PLN")
        uah = rates.get("UAH")

        if not all([usd, czk, pln]):
            return ""

        lines = ["💱 <b>КУРСИ ВАЛЮТ</b>"]
        lines.append(f"  €1 = <b>{usd:.4f}</b> $ · <b>{czk:.2f}</b> Kč · <b>{pln:.4f}</b> zł")
        if uah:
            lines.append(f"  €1 = <b>{uah:.2f}</b> ₴  |  $1 = <b>{uah/usd:.2f}</b> ₴")

        return "\n".join(lines)
    except Exception as e:
        print(f"get_currency_rates error: {e}")
        return ""


# ─── 3. СТРЕС-АЛЕРТ — комбінований сигнал ────────────────────────────────────

_STRESS_ALERT_FILE = os.path.join(_DATA_DIR, "monitor_stress_alert.json")

def check_stress_alert():
    """
    О 11:00 перевіряє комбінацію стрес-сигналів:
    - 3+ дні без бігу
    - вага росте 2+ дні
    - менше 7 год сну 2+ дні поспіль
    Якщо 2+ сигнали — надсилає AI мотивацію + конкретний план на сьогодні.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    if not (h == 11 and 0 <= m < 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    state = load_json_file(_STRESS_ALERT_FILE, default={})
    if state.get("last") == today:
        return

    signals = []
    details = []

    # Сигнал 1: дні без бігу
    try:
        import sys as _sys_sa; _sys_sa.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from strava import get_last_activity
        last_act = get_last_activity()
        if last_act:
            when = last_act.get("when", "")
            # Парсимо кількість днів
            days_no_run = 0
            if "дн." in when:
                import re as _re_sa
                m_re = _re_sa.search(r"(\d+) дн\.", when)
                if m_re:
                    days_no_run = int(m_re.group(1))
            elif when not in ("сьогодні", "вчора"):
                days_no_run = 5  # давно
            if days_no_run >= 3:
                signals.append("no_run")
                details.append(f"🏃 {days_no_run} дні без пробіжки")
        else:
            signals.append("no_run")
            details.append("🏃 Немає записаних тренувань")
    except Exception:
        pass

    # Сигнал 2: вага зростає
    try:
        from storage import load as _st_load_sa
        wdata = _st_load_sa("weight_data.json") or {}
        if wdata:
            sorted_keys = sorted(wdata.keys())[-4:]
            w_vals = [wdata[k] for k in sorted_keys if wdata.get(k)]
            if len(w_vals) >= 3:
                rising = sum(1 for i in range(len(w_vals)-1, 0, -1) if w_vals[i] > w_vals[i-1])
                if rising >= 2:
                    signals.append("weight_up")
                    details.append(f"⚖️ Вага росте {rising} дні (+{round(w_vals[-1]-w_vals[-rising-1],1)} кг)")
    except Exception:
        pass

    # Сигнал 3: звички (мало виконано)
    try:
        from habits import load_data as _hd_load_sa
        hab_db = _hd_load_sa()
        bad_days = 0
        for i in range(1, 4):
            day_k = (now_local - timedelta(days=i)).strftime("%Y-%m-%d")
            day_data = hab_db.get(day_k, {})
            done_count = sum(1 for v in day_data.values() if v is True)
            if done_count < 2:
                bad_days += 1
        if bad_days >= 2:
            signals.append("bad_habits")
            details.append(f"📋 Мало звичок {bad_days} дні поспіль")
    except Exception:
        pass

    if len(signals) < 2:
        return  # недостатньо сигналів

    # Відправляємо алерт
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    ai_plan = ""
    if gemini_key:
        try:
            import json as _json_sa, urllib.request as _ur_sa
            prompt = (
                f"Мої показники сьогодні: {'; '.join(details)}. "
                f"Це ознаки накопиченого стресу і зниженої енергії. "
                f"Дай конкретний план на СЬОГОДНІ (3-4 пункти, коротко) що робити прямо зараз щоб відновитись. "
                f"Реально і практично. Українською. Без зайвих слів і без привітань."
            )
            payload = _json_sa.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.6}
            }).encode()
            req_ai = _ur_sa.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}",
                data=payload, headers={"Content-Type": "application/json"}
            )
            with _ur_sa.urlopen(req_ai, timeout=12) as _resp_ai:
                ai_data = _json_sa.loads(_resp_ai.read())
            ai_plan = ai_data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            pass

    signal_text = "\n".join(f"  • {d}" for d in details)
    msg = (
        f"🔴 <b>СТРЕС-АЛЕРТ</b>\n\n"
        f"Бот помітив одночасно кілька тривожних сигналів:\n{signal_text}\n\n"
    )
    if ai_plan:
        msg += f"<b>План на сьогодні:</b>\n{ai_plan}"
    else:
        msg += (
            f"<b>Що зробити сьогодні:</b>\n"
            f"• Вийди на 20-хв прогулянку або пробіжку\n"
            f"• Випий 2 склянки води прямо зараз\n"
            f"• Лягай спати до 23:00\n"
            f"• Відмов собі від пізньої їжі"
        )

    send_telegram(msg)
    state["last"] = today
    save_json_file(_STRESS_ALERT_FILE, state)
    print(f"[Stress alert] sent: {signals}")


# ─── 4. МІСЯЧНИЙ ПІДСУМОК ─────────────────────────────────────────────────────

_MONTHLY_SUMMARY_FILE = os.path.join(_DATA_DIR, "monitor_monthly_summary.json")

def check_monthly_summary():
    """
    1-го числа о 09:00: повний місячний підсумок.
    Вага, km бігу, звички %, кроки, найкращі дні.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    if not (now_local.day == 1 and h == 9 and 0 <= m < 5):
        return

    month_key = now_local.strftime("%Y-%m")
    state = load_json_file(_MONTHLY_SUMMARY_FILE, default={})
    if state.get("last") == month_key:
        return

    # Минулий місяць
    prev_month_end = now_local.replace(day=1) - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    month_names = {
        1:"Січень",2:"Лютий",3:"Березень",4:"Квітень",5:"Травень",
        6:"Червень",7:"Липень",8:"Серпень",9:"Вересень",10:"Жовтень",
        11:"Листопад",12:"Грудень"
    }
    month_name = month_names[prev_month_end.month]

    lines = [f"📆 <b>ПІДСУМОК МІСЯЦЯ — {month_name} {prev_month_end.year}</b>\n"]

    # Вага
    try:
        from storage import load as _st_load_ms
        wdata = _st_load_ms("weight_data.json") or {}
        month_prefix = prev_month_end.strftime("%Y-%m")
        month_weights = {k: v for k, v in wdata.items() if k.startswith(month_prefix) and v}
        if month_weights:
            sorted_w = sorted(month_weights.items())
            w_start = sorted_w[0][1]
            w_end   = sorted_w[-1][1]
            w_delta = round(w_end - w_start, 1)
            sign = "+" if w_delta > 0 else ""
            trend = "📈 зросла" if w_delta > 0 else ("📉 знизилась" if w_delta < 0 else "➡️ без змін")
            lines.append(f"⚖️ <b>Вага:</b> {w_start}→{w_end} кг ({sign}{w_delta} кг) {trend}")
            to_goal = round(w_end - 78.0, 1)
            if to_goal > 0:
                lines.append(f"   До цілі 78 кг: ще <b>{to_goal} кг</b>")
            else:
                lines.append(f"   🏆 Ціль 78 кг досягнута!")
    except Exception:
        pass

    # Strava — пробіжки за місяць
    try:
        import sys as _sys_ms; _sys_ms.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from strava import _get_access_token
        import requests as _req_ms
        token = _get_access_token()
        import calendar as _cal
        _, last_day = _cal.monthrange(prev_month_end.year, prev_month_end.month)
        after_ts  = int(prev_month_start.replace(tzinfo=timezone.utc).timestamp())
        before_ts = int((prev_month_end.replace(day=last_day, hour=23, minute=59) + timedelta(seconds=1)).replace(tzinfo=timezone.utc).timestamp())
        r_ms = _req_ms.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100, "after": after_ts, "before": before_ts},
            timeout=15
        )
        acts = r_ms.json() if r_ms.ok else []
        runs = [a for a in acts if a.get("type") in ("Run","TrailRun","VirtualRun")]
        total_km = round(sum(a["distance"] for a in runs) / 1000, 1)
        total_min = sum(a["moving_time"] for a in runs) // 60
        lines.append(f"\n🏃 <b>Біг:</b> {len(runs)} пробіжок · <b>{total_km} км</b> · {total_min} хв")
        if total_km >= 100:
            lines.append("   🏅 100+ км за місяць — феноменально!")
        elif total_km >= 50:
            lines.append("   💪 50+ км — відмінно!")
        elif total_km >= 20:
            lines.append("   👍 Непогано, є куди рости")
        elif total_km > 0:
            lines.append("   ⚠️ Менше 20 км — наступний місяць більше!")
    except Exception as _e_ms_s:
        print(f"monthly strava error: {_e_ms_s}")

    # Звички
    try:
        from habits import load_data as _hd_ms, HABITS as _HABITS_MS
        hab_db = _hd_ms()
        month_prefix = prev_month_end.strftime("%Y-%m")
        month_days = [k for k in hab_db.keys() if k.startswith(month_prefix)]
        if month_days:
            lines.append(f"\n📋 <b>Звички за {len(month_days)} днів:</b>")
            all_habits = [{"id": "run", "name": "Біг", "emoji": "🏃"}] + _HABITS_MS
            for hab in all_habits[:5]:  # топ 5
                done = sum(1 for d in month_days if hab_db.get(d, {}).get(hab["id"]) is True)
                pct = int(done / len(month_days) * 100)
                bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                lines.append(f"  {hab['emoji']} {hab['name']}: [{bar}] {pct}%")
    except Exception:
        pass

    # Кроки
    try:
        from steps import load_steps_data as _lsd_ms
        sdata = _lsd_ms()
        month_prefix = prev_month_end.strftime("%Y-%m")
        month_steps = [v.get("steps", 0) for k, v in sdata.items() if k.startswith(month_prefix) and isinstance(v, dict)]
        if month_steps:
            avg_steps = int(sum(month_steps) / len(month_steps))
            total_steps = sum(month_steps)
            best_day = max(month_steps)
            lines.append(f"\n👟 <b>Кроки:</b> всього {total_steps:,} · середнє {avg_steps:,}/день")
            lines.append(f"   Найкращий день: {best_day:,} кроків")
    except Exception:
        pass

    # AI підсумок
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key and len(lines) > 2:
        try:
            import json as _json_ms, urllib.request as _ur_ms
            summary_data = " | ".join(lines[1:6])
            prompt = (
                f"Ось мої результати за {month_name}: {summary_data}. "
                f"Напиши коротку (2-3 речення) мотивуючу оцінку місяця і одну конкретну ціль на наступний місяць. "
                f"Українською, без привітань."
            )
            payload = _json_ms.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.7}
            }).encode()
            req_ai = _ur_ms.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}",
                data=payload, headers={"Content-Type": "application/json"}
            )
            with _ur_ms.urlopen(req_ai, timeout=12) as _resp_ai:
                ai_ms = _json_ms.loads(_resp_ai.read())
            ai_text = ai_ms["candidates"][0]["content"]["parts"][0]["text"].strip()
            lines.append(f"\n💬 <i>{ai_text}</i>")
        except Exception:
            pass

    # Додаємо повний ETF/акції блок
    try:
        etf_full = _get_etf_prices(full=True)
        if etf_full:
            lines.append(f"\n{etf_full}")
    except Exception as _e_etf_m:
        print(f"[monthly etf block] {_e_etf_m}")

    send_telegram("\n".join(lines))
    state["last"] = month_key
    save_json_file(_MONTHLY_SUMMARY_FILE, state)
    print("[Monthly summary] sent")

    # ── Графік місяця ──────────────────────────────────────────────────────────
    try:
        from charts import plot_monthly_dashboard as _plot_m
        mchart = _plot_m(prev_month_end.year, prev_month_end.month)
        if mchart:
            _send_photo_bytes(mchart, f"📊 {month_name} {prev_month_end.year} — місячний дашборд")
    except Exception as _e_mchart:
        print(f"monthly chart error: {_e_mchart}")


# ─── 5. ТИЖНЕВИЙ ДАШБОРД — команда /тиждень ──────────────────────────────────

def get_weekly_dashboard() -> str:
    """
    Зведений дашборд за поточний тиждень:
    біг, вага, звички, кроки — одним повідомленням.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    week_start = now_local - timedelta(days=now_local.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    lines = [
        f"📊 <b>ТИЖДЕНЬ {week_start.strftime('%d.%m')}–{now_local.strftime('%d.%m.%Y')}</b>\n"
    ]

    # 1. Біг
    try:
        import sys as _sys_wd; _sys_wd.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from strava import get_week_stats, get_last_activity
        wk = get_week_stats()
        la = get_last_activity()
        if wk:
            goal = 40
            pct = min(wk["km"] / goal, 1.0)
            filled = int(pct * 10)
            bar = "█" * filled + "░" * (10 - filled)
            lines.append(f"🏃 <b>Біг:</b> {wk['runs']} пробіжок · {wk['km']} км")
            lines.append(f"   [{bar}] {wk['km']}/{goal} км до цілі")
            if la:
                lines.append(f"   Остання: {la['distance_km']} км · {la['pace']} ({la['when']})")
        else:
            lines.append("🏃 <b>Біг:</b> немає даних")
    except Exception as _e_wd_r:
        lines.append(f"🏃 <b>Біг:</b> ⚠️ {_e_wd_r}")

    # 2. Вага
    try:
        from storage import load as _st_load_wd
        wdata = _st_load_wd("weight_data.json") or {}
        week_prefix = week_start.strftime("%Y-%m")
        week_weights = [(k, v) for k, v in sorted(wdata.items())
                        if k >= week_start.strftime("%Y-%m-%d") and v]
        if week_weights:
            w_first = week_weights[0][1]
            w_last  = week_weights[-1][1]
            delta   = round(w_last - w_first, 1)
            sign    = "+" if delta > 0 else ""
            trend   = "📈" if delta > 0.1 else ("📉" if delta < -0.1 else "➡️")
            lines.append(f"\n⚖️ <b>Вага:</b> {w_last} кг {trend} ({sign}{delta} кг за тиждень)")
            to_goal = round(w_last - 78.0, 1)
            if to_goal > 0:
                lines.append(f"   До 78 кг: -{to_goal} кг")
        else:
            lines.append("\n⚖️ <b>Вага:</b> немає записів цього тижня")
    except Exception:
        pass

    # 3. Звички
    try:
        from habits import load_data as _hd_wd, HABITS as _HAB_WD
        hab_db = _hd_wd()
        days_this_week = [(week_start + timedelta(days=i)).strftime("%Y-%m-%d")
                          for i in range(now_local.weekday() + 1)]
        days_done = {d: hab_db.get(d, {}) for d in days_this_week}

        lines.append(f"\n📋 <b>Звички</b> ({len(days_this_week)} днів):")
        all_habits = [{"id": "shower", "name": "Душ", "emoji": "🚿"}] + _HAB_WD
        for hab in all_habits:
            done  = sum(1 for d in days_this_week if days_done[d].get(hab["id"]) is True)
            total = len(days_this_week)
            icons = ""
            for d in days_this_week:
                v = days_done[d].get(hab["id"])
                icons += "✅" if v is True else ("❌" if v is False else "⬜")
            lines.append(f"  {hab['emoji']} {hab['name']}: {icons} {done}/{total}")
    except Exception:
        pass

    # 4. Кроки
    try:
        from steps import load_steps_data as _lsd_wd
        sdata = _lsd_wd()
        week_days = [(week_start + timedelta(days=i)).strftime("%Y-%m-%d")
                     for i in range(now_local.weekday() + 1)]
        step_vals = [sdata.get(d, {}).get("steps", 0) for d in week_days if isinstance(sdata.get(d), dict)]
        if step_vals:
            avg = int(sum(step_vals) / len(step_vals))
            total = sum(step_vals)
            lines.append(f"\n👟 <b>Кроки:</b> всього {total:,} · середнє {avg:,}/день")
    except Exception:
        pass

    return "\n".join(lines)


# ─── 6. КУРС ВАЛЮТ — додати в регулярний звіт ────────────────────────────────
# (функція get_currency_rates() вже визначена вище)
# Додаємо автоматичний виклик у check_smart_notifications або окремий watcher

_CURRENCY_ALERT_FILE = os.path.join(_DATA_DIR, "monitor_currency_alert.json")

def check_currency_alert():
    """
    Якщо EUR/USD змінився більш ніж на 0.5% за добу — надсилає алерт.
    Також щоранку о 08:00 надсилає поточні курси.
    """
    try:
        import urllib.request as _ur_ca, json as _json_ca

        now_local = datetime.now(timezone.utc) + timedelta(hours=2)
        h, m = now_local.hour, now_local.minute
        today = now_local.strftime("%Y-%m-%d")

        state = load_json_file(_CURRENCY_ALERT_FILE, default={})

        # Щоранку о 08:00 — просто відправляємо курси
        if h == 8 and 0 <= m < 5 and state.get("daily") != today:
            rates_text = get_currency_rates()
            if rates_text:
                send_telegram(rates_text)
                state["daily"] = today
                save_json_file(_CURRENCY_ALERT_FILE, state)
            return

        # Перевірка значного руху — кожну годину
        slot = f"{today}_{h}"
        if state.get("alert_slot") == slot:
            return

        url = "https://open.er-api.com/v6/latest/EUR"
        with _ur_ca.urlopen(url, timeout=8) as _r:
            data = _json_ca.loads(_r.read())

        if data.get("result") != "success":
            return

        rates = data.get("rates", {})
        usd = rates.get("USD", 0)

        last_usd = state.get("last_usd", usd)
        if last_usd and abs(usd - last_usd) / last_usd > 0.005:  # 0.5%+
            direction = "📈 зріс" if usd > last_usd else "📉 впав"
            pct = abs(usd - last_usd) / last_usd * 100
            send_telegram(
                f"💱 <b>Різкий рух EUR/USD!</b>\n"
                f"€1 = <b>{usd:.4f}$</b> ({direction} на {pct:.2f}%)\n"
                f"Попередній: {last_usd:.4f}$"
            )
            state["alert_slot"] = slot

        state["last_usd"] = usd
        save_json_file(_CURRENCY_ALERT_FILE, state)

    except Exception as e:
        print(f"check_currency_alert error: {e}")

