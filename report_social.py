#!/usr/bin/env python3
"""
Соціальний пост кожні 3 дні — інвестиції, крипто, фондовий ринок.
Тематика ротується: крипто → фондовий ринок → DeFi/стейблкоїни
Генерує картинку через matplotlib + надсилає в Telegram з емодзі.
"""

import os, json, time, io, random
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

try:
    import requests as _req
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]
_DATA_DIR      = os.path.dirname(os.path.abspath(__file__))
STATE_FILE     = os.path.join(_DATA_DIR, "social_post_state.json")

TOPICS = ["crypto", "stocks", "defi"]

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _get(url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            if _HAS_REQUESTS:
                r = _req.get(url, headers={"User-Agent": "social-report/1.0"}, timeout=25)
                r.raise_for_status()
                return r.json()
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "social-report/1.0"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    return json.loads(r.read().decode())
        except Exception as e:
            print(f"GET attempt {attempt}/{retries} [{url[:60]}]: {e}")
            if attempt < retries:
                time.sleep(3 * attempt)
    return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"last_post_date": "", "topic_index": 0}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def send_text(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text[:4090],
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }).encode()
    try:
        if _HAS_REQUESTS:
            _req.post(url, data=payload, headers={"Content-Type": "application/json"}, timeout=20)
        else:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print(f"send_text error: {e}")


def send_photo(image_bytes: bytes, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        if _HAS_REQUESTS:
            _req.post(url, files={"photo": ("chart.png", image_bytes, "image/png")},
                      data={"chat_id": TELEGRAM_CHAT, "caption": caption[:1024], "parse_mode": "HTML"},
                      timeout=30)
        else:
            # Простий multipart fallback
            boundary = "----boundary7890"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{TELEGRAM_CHAT}\r\n'
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption[:1024]}\r\n'
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'
                f"Content-Type: image/png\r\n\r\n"
            ).encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
            )
            urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"send_photo error: {e}")


# ─── ДАНІ ─────────────────────────────────────────────────────────────────────

def fetch_crypto_data():
    """Топ-8 крипто за ринковою капіталізацією."""
    data = _get(
        "https://api.coingecko.com/api/v3/coins/markets"
        "?vs_currency=usd&order=market_cap_desc&per_page=8&page=1"
        "&sparkline=false&price_change_percentage=24h"
    )
    if not data:
        return []
    result = []
    for c in data:
        result.append({
            "symbol": c["symbol"].upper(),
            "name": c["name"],
            "price": c["current_price"],
            "change_24h": c.get("price_change_percentage_24h") or 0,
            "mcap": c.get("market_cap") or 0,
        })
    return result


def fetch_stocks_data():
    """Індекси та топ акції через Yahoo Finance."""
    try:
        import yfinance as yf
        tickers_map = {
            "^GSPC": "S&P 500",
            "^IXIC": "NASDAQ",
            "^DJI": "Dow Jones",
            "AAPL": "Apple",
            "NVDA": "NVIDIA",
            "TSLA": "Tesla",
            "MSFT": "Microsoft",
            "AMZN": "Amazon",
        }
        result = []
        for ticker, name in tickers_map.items():
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period="2d")
                if len(hist) >= 2:
                    prev = hist["Close"].iloc[-2]
                    curr = hist["Close"].iloc[-1]
                    change = (curr - prev) / prev * 100
                    result.append({"symbol": ticker.replace("^", ""), "name": name, "price": curr, "change_24h": change})
            except Exception:
                pass
        return result
    except ImportError:
        return []


def fetch_defi_data():
    """TVL топ-8 DeFi протоколів."""
    data = _get("https://api.llama.fi/protocols")
    if not data:
        return []
    top = sorted(data, key=lambda x: x.get("tvl") or 0, reverse=True)[:8]
    result = []
    for p in top:
        change = p.get("change_1d") or 0
        result.append({
            "symbol": p.get("symbol", p["name"][:6]).upper(),
            "name": p["name"],
            "price": p.get("tvl") or 0,
            "change_24h": change,
        })
    return result


# ─── ГЕНЕРАЦІЯ КАРТИНКИ ───────────────────────────────────────────────────────

def _fmt_price(p, is_tvl=False):
    if is_tvl:
        if p >= 1e9: return f"${p/1e9:.1f}B"
        if p >= 1e6: return f"${p/1e6:.1f}M"
        return f"${p:.0f}"
    if p >= 1000: return f"${p:,.0f}"
    if p >= 1: return f"${p:.2f}"
    return f"${p:.4f}"


def generate_chart(items, title, subtitle, is_tvl=False):
    """Горизонтальний bar chart з темною темою."""
    if not _HAS_MPL or not items:
        return None

    fig, ax = plt.subplots(figsize=(9, 5.5))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#161b22")

    symbols = [d["symbol"] for d in items]
    changes = [d["change_24h"] for d in items]
    prices  = [d["price"] for d in items]

    colors = ["#00d46a" if c >= 0 else "#ff4d4d" for c in changes]

    bars = ax.barh(symbols, changes, color=colors, height=0.55, edgecolor="none")

    # Підписи значень
    for i, (bar, price, chg) in enumerate(zip(bars, prices, changes)):
        price_str = _fmt_price(price, is_tvl)
        chg_str   = f"{'+' if chg >= 0 else ''}{chg:.2f}%"
        ax.text(
            bar.get_width() + (0.05 if chg >= 0 else -0.05),
            bar.get_y() + bar.get_height() / 2,
            f"  {price_str}  {chg_str}",
            va="center", ha="left" if chg >= 0 else "right",
            color="#e6edf3", fontsize=8.5, fontweight="bold"
        )

    # Вертикальна лінія 0
    ax.axvline(0, color="#30363d", linewidth=1)

    ax.set_xlabel("Зміна за 24г (%)", color="#8b949e", fontsize=9)
    ax.tick_params(axis="y", colors="#e6edf3", labelsize=9.5)
    ax.tick_params(axis="x", colors="#8b949e", labelsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#30363d")
    ax.spines["bottom"].set_color("#30363d")

    # Заголовок
    fig.text(0.5, 0.97, title, ha="center", va="top", color="#e6edf3",
             fontsize=13, fontweight="bold")
    fig.text(0.5, 0.92, subtitle, ha="center", va="top", color="#8b949e", fontsize=9)

    # Watermark
    fig.text(0.98, 0.01, f"Дані: {datetime.now(timezone.utc).strftime('%d.%m.%Y')}",
             ha="right", va="bottom", color="#484f58", fontsize=7)

    plt.tight_layout(rect=[0, 0.03, 1, 0.90])

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ─── ПОСТИ ────────────────────────────────────────────────────────────────────

def make_crypto_post():
    items = fetch_crypto_data()
    if not items:
        return None, None

    local = datetime.now(timezone.utc) + timedelta(hours=2)
    date_str = local.strftime("%d.%m.%Y")

    # Топ-3 зростання
    gainers = sorted(items, key=lambda x: x["change_24h"], reverse=True)[:3]
    losers  = sorted(items, key=lambda x: x["change_24h"])[:2]

    g_lines = "\n".join(
        f"  📈 <b>{g['symbol']}</b> {'+' if g['change_24h']>0 else ''}{g['change_24h']:.2f}% → {_fmt_price(g['price'])}"
        for g in gainers
    )
    l_lines = "\n".join(
        f"  📉 <b>{l['symbol']}</b> {l['change_24h']:.2f}%"
        for l in losers
    )

    btc = next((x for x in items if x["symbol"] == "BTC"), None)
    eth = next((x for x in items if x["symbol"] == "ETH"), None)
    btc_line = f"₿ BTC: <b>{_fmt_price(btc['price'])}</b> ({'+' if btc['change_24h']>0 else ''}{btc['change_24h']:.2f}%)" if btc else ""
    eth_line = f"Ξ ETH: <b>{_fmt_price(eth['price'])}</b> ({'+' if eth['change_24h']>0 else ''}{eth['change_24h']:.2f}%)" if eth else ""

    tips = random.choice([
        "💡 <i>Диверсифікація — ключ до стабільного портфеля.</i>",
        "💡 <i>DCA (усереднення вартості) — перевірена стратегія в волатильному ринку.</i>",
        "💡 <i>Не інвестуй більше, ніж готовий втратити. Крипто — це ризик.</i>",
        "💡 <i>HODL — легко казати, важко тримати. Але саме терпіння творить прибуток.</i>",
        "💡 <i>Bitcoin — цифрове золото. ETH — децентралізований комп'ютер.</i>",
    ])

    text = (
        f"🪙 <b>Крипторинок сьогодні</b> · {date_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{btc_line}\n{eth_line}\n\n"
        f"🚀 <b>Лідери зростання:</b>\n{g_lines}\n\n"
        f"🔻 <b>Під тиском:</b>\n{l_lines}\n\n"
        f"{tips}\n\n"
        f"🔔 <i>Стеж за ринком щодня — маленькі кроки до великих результатів.</i>\n"
        f"#крипто #bitcoin #ethereum #інвестиції #crypto"
    )

    chart = generate_chart(
        items, "Крипторинок — топ 8",
        f"Зміна за 24г · {date_str}"
    )
    return text, chart


def make_stocks_post():
    items = fetch_stocks_data()
    local = datetime.now(timezone.utc) + timedelta(hours=2)
    date_str = local.strftime("%d.%m.%Y")

    if not items:
        text = (
            f"📊 <b>Фондовий ринок</b> · {date_str}\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"⚠️ Не вдалося отримати дані по акціях сьогодні.\n\n"
            f"💡 <i>Пам'ятай: ринок завжди відкривається знову. Volatile market = нові можливості.</i>\n"
            f"#фондовийринок #інвестиції #акції #stocks"
        )
        return text, None

    gainers = sorted(items, key=lambda x: x["change_24h"], reverse=True)[:3]
    losers  = sorted(items, key=lambda x: x["change_24h"])[:2]

    g_lines = "\n".join(
        f"  📈 <b>{g['symbol']}</b> {'+' if g['change_24h']>0 else ''}{g['change_24h']:.2f}%"
        for g in gainers
    )
    l_lines = "\n".join(
        f"  📉 <b>{l['symbol']}</b> {l['change_24h']:.2f}%"
        for l in losers
    )

    sp = next((x for x in items if x["symbol"] == "GSPC"), None)
    sp_line = f"📊 S&P 500: <b>{_fmt_price(sp['price'])}</b> ({'+' if sp['change_24h']>0 else ''}{sp['change_24h']:.2f}%)" if sp else ""

    tip = random.choice([
        "💡 <i>Індексні фонди — найпростіший спосіб інвестувати в ринок.</i>",
        "💡 <i>Час на ринку важливіший за тайминг ринку (Time in market > Timing the market).</i>",
        "💡 <i>NVIDIA та AI-сектор продовжують змінювати технологічний ландшафт.</i>",
        "💡 <i>Диверсифікація між акціями та крипто знижує загальний ризик портфеля.</i>",
        "💡 <i>Рецесія або ріст — довгостроковий інвестор завжди у плюсі через 10 років.</i>",
    ])

    text = (
        f"📈 <b>Фондовий ринок</b> · {date_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{sp_line}\n\n"
        f"🏆 <b>Лідери дня:</b>\n{g_lines}\n\n"
        f"🔻 <b>Під тиском:</b>\n{l_lines}\n\n"
        f"{tip}\n\n"
        f"🔔 <i>Інвестуй регулярно — навіть малі суми створюють великий капітал.</i>\n"
        f"#акції #фондовийринок #інвестиції #nasdaq #sp500"
    )

    chart = generate_chart(items[:8], "Фондовий ринок — акції & індекси", f"Зміна за день · {date_str}")
    return text, chart


def make_defi_post():
    items = fetch_defi_data()
    local = datetime.now(timezone.utc) + timedelta(hours=2)
    date_str = local.strftime("%d.%m.%Y")

    if not items:
        text = (
            f"🏦 <b>DeFi & Web3</b> · {date_str}\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"⚠️ DeFiLlama недоступна. Спробую пізніше.\n"
            f"#defi #web3 #blockchain"
        )
        return text, None

    total_tvl = sum(x["price"] for x in items)
    top_proto = items[0]["name"] if items else "—"

    gainers = sorted(items, key=lambda x: x["change_24h"], reverse=True)[:3]
    g_lines = "\n".join(
        f"  📈 <b>{g['name'][:15]}</b> {'+' if g['change_24h']>0 else ''}{g['change_24h']:.2f}%"
        for g in gainers
    )

    def fmt_tvl(v):
        if v >= 1e9: return f"${v/1e9:.1f}B"
        return f"${v/1e6:.0f}M"

    tip = random.choice([
        "💡 <i>DeFi дозволяє заробляти відсотки без банків — але ризики smart contract реальні.</i>",
        "💡 <i>Liquid staking (Lido, EigenLayer) — один з найпопулярніших способів отримати yield у 2025.</i>",
        "💡 <i>RWA (реальні активи в блокчейні) — тренд, що об'єднує TradFi і DeFi.</i>",
        "💡 <i>Перед yield farming — завжди перевіряй аудит смарт-контракту.</i>",
        "💡 <i>TVL (Total Value Locked) — головна метрика здоров'я DeFi-протоколу.</i>",
    ])

    text = (
        f"🏦 <b>DeFi сьогодні</b> · {date_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 Загальний TVL топ-8: <b>{fmt_tvl(total_tvl)}</b>\n"
        f"🥇 Лідер: <b>{top_proto}</b> ({fmt_tvl(items[0]['price'])})\n\n"
        f"🚀 <b>Зростання TVL:</b>\n{g_lines}\n\n"
        f"{tip}\n\n"
        f"🔔 <i>DeFi змінює фінанси — будь у курсі протоколів майбутнього.</i>\n"
        f"#defi #web3 #blockchain #yield #інвестиції"
    )

    chart = generate_chart(items, "DeFi — топ 8 протоколів (TVL)", f"Зміна за 24г · {date_str}", is_tvl=True)
    return text, chart


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def should_post(state):
    """Перевіряє чи треба постити сьогодні (раз на 3 дні)."""
    last = state.get("last_post_date", "")
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d")
        today_dt = datetime.strptime(today, "%Y-%m-%d")
        return (today_dt - last_dt).days >= 3
    except Exception:
        return True


def main():
    state = load_state()

    if not should_post(state):
        print("Social post: skipping, not 3 days yet.")
        return

    topic_index = state.get("topic_index", 0) % len(TOPICS)
    topic = TOPICS[topic_index]
    print(f"=== Social post: topic={topic} ===")

    if topic == "crypto":
        text, chart = make_crypto_post()
    elif topic == "stocks":
        text, chart = make_stocks_post()
    else:
        text, chart = make_defi_post()

    if not text:
        print("No data, skipping post.")
        return

    if chart:
        send_photo(chart, text)
    else:
        send_text(text)

    # Оновлюємо стан
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
    state["last_post_date"] = today
    state["topic_index"] = (topic_index + 1) % len(TOPICS)
    save_state(state)

    print(f"Social post sent: topic={topic}, next={TOPICS[(topic_index + 1) % len(TOPICS)]}")


if __name__ == "__main__":
    main()
