#!/usr/bin/env python3
"""
openmind.py — ВІДКРИТІСТЬ ДО НОВОГО.

Проблема, яку вирішує: бот був прибитий цвяхами до вузького списку (4-8 монет
Олега, ті самі теми). Тут усе динамічне:

  1. top_coins()   — ТОП-20 CoinGecko за капіталізацією НА ЗАРАЗ (список сам
                     змінюється, нічого не хардкодимо).
  2. movers()      — найбільші рухи по всьому ТОП-100, а не лише по «своїх».
  3. trending()    — що зараз шукають люди (CoinGecko /search/trending):
                     монети, яких у списках Олега немає взагалі.
  4. newcomers()   — монети, які ВПЕРШЕ зайшли в ТОП-20 (порівняння зі станом).
  5. news()        — свіжі новини з кількох RSS (Cointelegraph, CoinDesk,
                     Decrypt, The Block) — по всьому ринку, не по 4 монетах.
  6. horizon()     — тема дня ПОЗА звичними інтересами: ротація з широкого
                     пулу (наука, технології, макро, психологія, мова, спорт,
                     подорожі, ремесла, історія…), щоб не варитись в одному.
  7. digest()      — усе разом + AI-аналіз ринку і одне «спробуй нове».

Стан: openmind_state.json (щоб бачити newcomers і не повторювати тему дня).
"""
import os
import sys
import json
import re
import urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K

TAG = "openmind"
STATE_FILE = "openmind_state.json"

CG = "https://api.coingecko.com/api/v3"
UA = {"User-Agent": "Mozilla/5.0"}

RSS = [
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),
]


def _now():
    return K.now()


def _log(m):
    K.log(TAG, m)


def _get(url: str, timeout: int = 12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url: str, timeout: int = 12):
    try:
        return json.loads(_get(url, timeout))
    except Exception as e:
        _log("GET " + url.split("?")[0] + " error: " + str(e))
        return None


# ─── КРИПТО: ДИНАМІЧНИЙ ТОП ──────────────────────────────────────────────────

def market(top: int = 100) -> list:
    """ТОП-N за капіталізацією прямо зараз. Нічого не захардкоджено."""
    url = (CG + "/coins/markets?vs_currency=usd&order=market_cap_desc&per_page="
           + str(min(250, top)) + "&page=1&price_change_percentage=24h,7d")
    data = _json(url, 15)
    return data if isinstance(data, list) else []


def top_coins(n: int = 20, data: list = None) -> list:
    data = data if data is not None else market(n)
    return data[:n]


def movers(data: list = None, n: int = 5) -> dict:
    """Найбільші рухи по ВСЬОМУ ТОП-100 — включно з монетами, яких Олег не тримає."""
    data = data if data is not None else market(100)
    rows = [c for c in data if c.get("price_change_percentage_24h") is not None]
    rows.sort(key=lambda c: c["price_change_percentage_24h"], reverse=True)
    return {"up": rows[:n], "down": list(reversed(rows[-n:]))}


def trending(n: int = 7) -> list:
    """Що шукають ЗАРАЗ — джерело нових ідей поза звичним списком."""
    data = _json(CG + "/search/trending", 12) or {}
    out = []
    for it in (data.get("coins") or [])[:n]:
        c = it.get("item") or {}
        out.append({"symbol": str(c.get("symbol", "")).upper(),
                    "name": c.get("name", ""),
                    "rank": c.get("market_cap_rank"),
                    "id": c.get("id", "")})
    return out


def newcomers(data: list = None) -> list:
    """Хто ВПЕРШЕ (від останньої перевірки) зайшов у ТОП-20."""
    cur = [str(c.get("symbol", "")).upper() for c in top_coins(20, data)]
    st = K.load(STATE_FILE, default={}) or {}
    if not isinstance(st, dict):
        st = {}
    prev = st.get("top20") or []
    fresh = [s for s in cur if s and s not in prev] if prev else []
    st["top20"] = cur
    st["top20_at"] = _now().isoformat(timespec="seconds")
    K.save(STATE_FILE, st)
    return fresh


def _fmt_coin(c: dict) -> str:
    sym = str(c.get("symbol", "")).upper()
    price = c.get("current_price")
    ch = c.get("price_change_percentage_24h")
    ch7 = c.get("price_change_percentage_7d_in_currency")
    if price is None:
        return sym
    p = ("${:,.4f}".format(price) if price < 1 else "${:,.0f}".format(price))
    out = sym + " " + p
    if ch is not None:
        out += " (" + ("+" if ch > 0 else "") + "{:.1f}%".format(ch)
        if ch7 is not None:
            out += " | 7д " + ("+" if ch7 > 0 else "") + "{:.1f}%".format(ch7)
        out += ")"
    return out


def crypto_block(data: list = None) -> str:
    """ТОП-20 + рухи + трендове + новачки — усе фактами, без вибірковості."""
    data = data if data is not None else market(100)
    if not data:
        return "Крипто: дані CoinGecko недоступні"
    rows = ["ТОП-20 за капіталізацією (зараз):"]
    for i, c in enumerate(top_coins(20, data), 1):
        rows.append(str(i) + ". " + _fmt_coin(c))
    mv = movers(data)
    rows.append("")
    rows.append("Найбільші рухи 24г по ТОП-100:")
    rows.append("вгору: " + ", ".join(_fmt_coin(c) for c in mv["up"]))
    rows.append("вниз: " + ", ".join(_fmt_coin(c) for c in mv["down"]))
    tr = trending()
    if tr:
        rows.append("")
        rows.append("Трендове зараз (пошук CoinGecko): "
                    + ", ".join((t["symbol"] + " " + t["name"]
                                 + (" #" + str(t["rank"]) if t["rank"] else ""))
                                for t in tr))
    nc = newcomers(data)
    if nc:
        rows.append("НОВЕ в ТОП-20 від останньої перевірки: " + ", ".join(nc))
    return "\n".join(rows)


# ─── НОВИНИ ПО ВСЬОМУ РИНКУ ──────────────────────────────────────────────────

def news(limit: int = 12) -> list:
    """Свіжі новини з кількох джерел. Без фільтра «тільки мої монети»."""
    out = []
    for src, url in RSS:
        try:
            raw = _get(url, 12).decode("utf-8", "replace")
        except Exception as e:
            _log("rss " + src + ": " + str(e))
            continue
        items = re.findall(r"<item>(.*?)</item>", raw, re.S)[:6]
        for it in items:
            t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
            if not t:
                continue
            title = re.sub(r"<[^>]+>", "", t.group(1)).strip()
            if title:
                out.append({"src": src, "title": title[:180]})
    return out[:limit]


def news_block(items: list = None) -> str:
    items = items if items is not None else news()
    if not items:
        return "Новини: джерела недоступні"
    return "\n".join("• [" + i["src"] + "] " + i["title"] for i in items)


# ─── ГОРИЗОНТ: ТЕМА ПОЗА ЗВИЧНИМИ ІНТЕРЕСАМИ ─────────────────────────────────

HORIZON = [
    "наука і космос — свіжа ідея, яку легко зрозуміти",
    "технології поза крипто: AI-інструменти, роботи, чипи",
    "макроекономіка світу: ставки, інфляція, ринки праці",
    "психологія рішень і когнітивні викривлення",
    "історія — епізод, який пояснює щось про сьогодні",
    "мова: словацька або англійська — корисна конструкція",
    "спорт поза бігом: сила, мобільність, відновлення",
    "їжа і нутриціологія без дієтичних міфів",
    "гроші поза крипто: ETF, облігації, нерухомість, пенсія",
    "кар'єра і навички, які дорожчають у наступні 5 років",
    "подорожі: місце в 3-5 годинах від Кошиць, варте вихідного",
    "ремесло чи хобі, яке дає результат руками",
    "книга або довгий текст, який варто прочитати цього тижня",
    "музика чи кіно, яких Олег скоріше за все не чув/не бачив",
    "інженерія і виробництво — щось із його ж галузі, але нове",
    "довголіття: що реально працює за дослідженнями",
    "фінансова безпека: шахрайство, приватність, гігієна даних",
    "філософія/стоїцизм — практичний висновок, не цитата",
    "енергетика і клімат: як це впливає на рахунки в Словаччині",
    "локальне: Кошице і Словаччина — подія, зміна, можливість",
]


def horizon() -> str:
    """Тема дня поза звичним колом. Ротація без повторів, поки пул не пройдено."""
    st = K.load(STATE_FILE, default={}) or {}
    if not isinstance(st, dict):
        st = {}
    used = st.get("horizon_used") or []
    pool = [t for t in HORIZON if t not in used]
    if not pool:
        pool = list(HORIZON)
        used = []
    idx = (_now().timetuple().tm_yday + _now().hour) % len(pool)
    pick = pool[idx]
    used.append(pick)
    st["horizon_used"] = used[-len(HORIZON):]
    st["horizon_last"] = pick
    st["horizon_day"] = _now().strftime("%Y-%m-%d")
    K.save(STATE_FILE, st)
    return pick


# ─── ДАЙДЖЕСТ ────────────────────────────────────────────────────────────────

_STYLE = (
    "Ти аналітик і співрозмовник Олега (37 р., Кошице, змінний графік, цікавиться "
    "інвестиціями, бігом, здоров'ям). Пиши українською, конкретно, з цифрами з "
    "даних. ЗАБОРОНЕНО вигадувати числа й новини, яких немає в даних. "
    "НЕ звужуйся до BTC/ETH/AVAX/ONDO — дивись на весь ринок і на те, що для "
    "Олега поки НОВЕ. Незгода і незручний висновок цінніші за приємний."
)


def digest(send: bool = True) -> str:
    data = market(100)
    cb = crypto_block(data)
    nb = news_block()
    hz = horizon()

    prompt = (_STYLE + "\n\nДАНІ РИНКУ:\n" + cb + "\n\nНОВИНИ:\n" + nb
              + "\n\nТЕМА ДЛЯ РОЗШИРЕННЯ ГОРИЗОНТУ: " + hz + "\n\n"
              "Зроби ОГЛЯД:\n"
              "1) 🌍 РИНОК — що насправді відбувається по ТОП-20 і по рухах, "
              "одним абзацом, з цифрами\n"
              "2) 📰 3 ГОЛОВНІ НОВИНИ — чому саме вони важливі (з наданих)\n"
              "3) 👀 НА ЩО ОЛЕГ НЕ ДИВИТЬСЯ — 2-3 монети/сектори з даних, яких "
              "немає в його звичному списку, і чому варто помітити. Без хайпу: "
              "якщо це смітник — так і скажи\n"
              "4) ⚠️ РИЗИК ТИЖНЯ — один, конкретний\n"
              "5) 🧭 ГОРИЗОНТ — коротко і цікаво розкрий тему «" + hz + "»: "
              "один факт або ідея + чому це може бути корисно саме йому. "
              "Без води, 3-5 речень\n"
              "6) 🎯 ОДНА ДІЯ — що зробити сьогодні (не «подумати»)")
    body = K.gemini_text(prompt, max_tokens=1700, temperature=0.85, tag=TAG) or ""
    body = body.strip() or "AI недоступний — нижче лише факти ринку."

    txt = ("🌐 <b>ВІДКРИТИЙ ОГЛЯД</b>\n" + _now().strftime("%d.%m.%Y %H:%M")
           + "\n\n" + body + "\n\n<b>ФАКТИ</b>\n" + cb)
    if send:
        K.send_card(txt, _kb(), tag=TAG)
        try:
            import selfact
            selfact.journal(TAG, "digest", "відкритий огляд ринку + горизонт", hz)
        except Exception:
            pass
    return txt


def _kb():
    return [
        [{"text": "🔥 Трендове", "callback_data": "om_trend"},
         {"text": "📰 Новини", "callback_data": "om_news"}],
        [{"text": "🧭 Ще тема", "callback_data": "om_horizon"},
         {"text": "📊 ТОП-20", "callback_data": "om_top"}],
    ]


def top_text() -> str:
    return "📊 <b>ТОП-20 і рухи ринку</b>\n\n" + crypto_block()


def trend_text() -> str:
    tr = trending()
    if not tr:
        return "🔥 Трендове: дані недоступні"
    rows = ["🔥 <b>Трендове зараз</b> (те, чого немає в твоєму списку)", ""]
    for t in tr:
        rows.append("• " + t["symbol"] + " — " + t["name"]
                    + (" (#" + str(t["rank"]) + " за капою)" if t["rank"] else ""))
    return "\n".join(rows)


def horizon_text() -> str:
    hz = horizon()
    prompt = (_STYLE + "\n\nТЕМА: " + hz + "\n\n"
              "Розкрий цю тему для Олега: один конкретний факт або ідея, "
              "чому це цікаво і що з цим робити. 4-6 речень, без води. "
              "Якщо потрібні числа, яких у тебе немає — не вигадуй, "
              "говори якісно.")
    body = (K.gemini_text(prompt, max_tokens=700, temperature=0.9, tag=TAG)
            or "AI недоступний.")
    return "🧭 <b>ГОРИЗОНТ</b>\n<i>" + K.esc(hz) + "</i>\n\n" + body.strip()


# ─── ПЛАНУВАЛЬНИК ────────────────────────────────────────────────────────────

def tick() -> str:
    """Відкритий огляд двічі на день: 09:30 і 18:30."""
    now = _now()
    day = now.strftime("%Y-%m-%d")
    st = K.load(STATE_FILE, default={}) or {}
    if not isinstance(st, dict):
        st = {}
    slots = {"am": (9, 30), "pm": (18, 30)}
    done = []
    try:
        import quiet
        if quiet.blocked("msg"):
            return ""
    except Exception:
        pass
    for name, (h, m) in slots.items():
        if now.hour == h and m <= now.minute < m + 6 and st.get(name) != day:
            try:
                digest(send=True)
                st[name] = day
                done.append(name)
            except Exception as e:
                _log(name + " error: " + str(e))
    if done:
        K.save(STATE_FILE, st)
    return ", ".join(done)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "top"
    if cmd == "digest":
        print(digest(send=False))
    elif cmd == "trend":
        print(trend_text())
    elif cmd == "news":
        print(news_block())
    elif cmd == "horizon":
        print(horizon())
    else:
        print(top_text())
