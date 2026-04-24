#!/usr/bin/env python3
"""
Соціальний пост кожні 3 дні — крипто, фондовий ринок, DeFi.
Стиль: короткий інсайт-пост, жива мова, без зайвих цифр.
"""

import os, json, time, random
import urllib.request
from datetime import datetime, timezone, timedelta

try:
    import requests as _req
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]
_DATA_DIR      = os.path.dirname(os.path.abspath(__file__))
STATE_FILE     = os.path.join(_DATA_DIR, "social_post_state.json")

TOPICS = ["crypto", "stocks", "defi"]

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _get(url):
    try:
        if _HAS_REQUESTS:
            r = _req.get(url, headers={"User-Agent": "social/1.0"}, timeout=20)
            r.raise_for_status()
            return r.json()
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "social/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
    except Exception as e:
        print(f"GET error [{url[:60]}]: {e}")
        return None

TOPIC_IMAGES = {
    "crypto": "https://storage.googleapis.com/runable-templates/cli-uploads%2F1zsprqn6ymqOFgAJnNEK2HbTycMPBvLc%2F5W3elrxxuw6OAmJPUBKgk%2Fbitcoin-crypto-investment-site-unsplash-com_5.jpg",
    "defi":   "https://storage.googleapis.com/runable-templates/cli-uploads%2F1zsprqn6ymqOFgAJnNEK2HbTycMPBvLc%2FVsnXOI79PSD7WNiNEcYyj%2Fdefi-blockchain-finance-site-unsplash-com_1.jpg",
    "stocks": "https://storage.googleapis.com/runable-templates/cli-uploads%2F1zsprqn6ymqOFgAJnNEK2HbTycMPBvLc%2FRI3Pa6ro9nbBIy2csiEip%2Fstock-market-investment-site-unsplash-com_0.jpg",
}

def send_post(text, topic):
    """Надсилає пост з картинкою."""
    img_url = TOPIC_IMAGES.get(topic)
    if img_url:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT,
            "photo": img_url,
            "caption": text[:1024],
            "parse_mode": "HTML",
        }).encode()
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT,
            "text": text[:4090],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode()
    try:
        if _HAS_REQUESTS:
            _req.post(url, data=payload, headers={"Content-Type": "application/json"}, timeout=20)
        else:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print(f"send_post error: {e}")

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {"last_post_date": "", "topic_index": 0}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def should_post(state):
    last  = state.get("last_post_date", "")
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
    if not last:
        return True
    try:
        last_dt  = datetime.strptime(last, "%Y-%m-%d")
        today_dt = datetime.strptime(today, "%Y-%m-%d")
        return (today_dt - last_dt).days >= 3
    except:
        return True


# ─── ДАНІ ─────────────────────────────────────────────────────────────────────

def get_btc_change():
    data = _get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true")
    if not data:
        return None, None, None, None
    btc_price  = data.get("bitcoin", {}).get("usd")
    btc_change = data.get("bitcoin", {}).get("usd_24h_change")
    eth_change = data.get("ethereum", {}).get("usd_24h_change")
    return btc_price, btc_change, eth_change, data

def get_defi_tvl():
    data = _get("https://api.llama.fi/protocols")
    if not data:
        return None, None
    total = sum(p.get("tvl") or 0 for p in data)
    top   = sorted(data, key=lambda x: x.get("tvl") or 0, reverse=True)[0]
    return total, top.get("name", "")


# ─── ПОСТИ ────────────────────────────────────────────────────────────────────

def make_crypto_post():
    btc_price, btc_change, eth_change, _ = get_btc_change()

    btc_mood = ""
    if btc_change is not None:
        if btc_change > 3:
            btc_mood = "бики взяли контроль 🐂"
        elif btc_change > 0:
            btc_mood = "ринок у плюсі 📈"
        elif btc_change > -3:
            btc_mood = "невелика корекція 😐"
        else:
            btc_mood = "ведмеді тиснуть 🐻"

    btc_str = f"${btc_price:,.0f}" if btc_price else "—"
    ch_str  = f"{'+' if (btc_change or 0) > 0 else ''}{btc_change:.1f}%" if btc_change else ""

    INSIGHTS = [
        (
            "Чому Bitcoin — це не просто монета\n\n"
            "BTC вирішує реальну проблему: зберігання вартості без дозволу банків чи урядів.\n\n"
            "→ Фіксована емісія: 21 млн монет. Ніколи більше.\n"
            "→ Працює 24/7 без вихідних і святкових\n"
            "→ Переказ $1M займає хвилини і коштує центи\n\n"
            "💡 Bitcoin — не інвестиція в компанію. Це інвестиція в протокол, якому довіряє весь світ.\n\n"
            "Ти вже тримаєш BTC у портфелі?"
        ),
        (
            "DCA — найпростіша стратегія яка працює\n\n"
            "Не намагайся вгадати дно. Просто купуй регулярно.\n\n"
            "→ $100 на тиждень в BTC протягом 3 років = стабільний середній курс\n"
            "→ Емоції прибрані з рівняння\n"
            "→ Ринок волатильний — твоя стратегія ні\n\n"
            "💡 Найбільші збитки в крипто — у тих хто купив на піку і продав на дні зі страху.\n\n"
            "DCA захищає від цього. Спробуй."
        ),
        (
            "Альткоїни: можливість чи пастка?\n\n"
            "90% альткоїнів зникнуть. Але 10% змінять індустрію.\n\n"
            "→ Ethereum — розумні контракти і DeFi\n"
            "→ Solana — швидкість і дешеві транзакції\n"
            "→ ONDO — токенізація реальних активів (RWA)\n\n"
            "💡 Правило: не вкладай в те що не можеш пояснити за 30 секунд.\n\n"
            "Який альткоїн в твоєму портфелі і чому?"
        ),
    ]

    insight = random.choice(INSIGHTS)
    price_line = f"📊 BTC зараз: <b>{btc_str}</b> {ch_str} — {btc_mood}\n┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n" if btc_price else ""

    text = (
        f"₿ <b>{insight.split(chr(10))[0]}</b>\n\n"
        f"{chr(10).join(insight.split(chr(10))[1:])}\n\n"
        f"{price_line}"
        f"#bitcoin #крипто #інвестиції #crypto #btc"
    )
    return text


def make_stocks_post():
    INSIGHTS = [
        (
            "Чому індексні фонди б'ють більшість інвесторів\n\n"
            "Warren Buffett сперечався на $1M що S&P 500 обжене будь-який хедж-фонд за 10 років.\n"
            "Він виграв.\n\n"
            "→ 90% активних менеджерів не обганяють індекс\n"
            "→ Комісії з'їдають прибуток\n"
            "→ Час на ринку важливіший за тайминг\n\n"
            "💡 VOO, SPY, QQQ — три тікери які варто знати кожному хто інвестує довгостроково.\n\n"
            "Ти тримаєш індексні фонди?"
        ),
        (
            "NVIDIA — компанія яка стала центром AI революції\n\n"
            "Ще 5 років тому NVDA робила відеокарти для геймерів.\n"
            "Сьогодні — вона постачає мізки для ChatGPT, Tesla і кожного дата-центру.\n\n"
            "→ Виручка зросла в 5x за 2 роки\n"
            "→ Кожна AI компанія у черзі за їхніми GPU\n"
            "→ H100, H200, Blackwell — продукти що визначають майбутнє\n\n"
            "💡 Інвестуєш в AI? Можливо ти вже інвестуєш через NVDA.\n\n"
            "Яка AI-акція у твоєму портфелі?"
        ),
        (
            "Рецесія наближається? Ось що робити\n\n"
            "Ніхто не знає коли буде рецесія. Але підготуватись можна вже зараз.\n\n"
            "→ Cash reserve 3-6 місяців витрат — основа основ\n"
            "→ Облігації та золото як захисні активи\n"
            "→ Дивідендні акції — платять навіть коли ринок падає\n\n"
            "💡 Ринок впаде. Ринок відновиться. Це відбувалось 100 разів і відбудеться знову.\n\n"
            "Твій портфель готовий до турбулентності?"
        ),
    ]

    insight = random.choice(INSIGHTS)
    text = (
        f"📈 <b>{insight.split(chr(10))[0]}</b>\n\n"
        f"{chr(10).join(insight.split(chr(10))[1:])}\n\n"
        f"#акції #інвестиції #фондовийринок #stocks #nasdaq"
    )
    return text


def make_defi_post():
    total_tvl, top_proto = get_defi_tvl()

    tvl_str = ""
    if total_tvl:
        if total_tvl >= 1e9:
            tvl_str = f"${total_tvl/1e9:.0f}B"
        else:
            tvl_str = f"${total_tvl/1e6:.0f}M"

    INSIGHTS = [
        (
            "DeFi: банківські послуги без банків\n\n"
            "Уяви що ти можеш:\n"
            "→ Отримати кредит без довідки про доходи\n"
            "→ Заробляти відсотки на стейблкоїнах без банку\n"
            "→ Торгувати активами 24/7 без брокера\n\n"
            "Це не майбутнє — це DeFi вже сьогодні.\n\n"
            f"💡 Загальний TVL в DeFi: <b>{tvl_str}</b>. Люди довіряють протоколам мільярди доларів.\n\n"
            "Ти вже пробував DeFi?"
        ),
        (
            "RWA — найгарячіший тренд в крипто\n\n"
            "Real World Assets (реальні активи в блокчейні) — це коли:\n"
            "→ Держоблігації США торгуються як токени\n"
            "→ Нерухомість можна купити частками за $100\n"
            "→ Золото, акції, кредити — все в одному гаманці\n\n"
            "Протоколи як ONDO, Maple, Centrifuge вже роблять це реальністю.\n\n"
            "💡 TradFi і DeFi зливаються. Хто розуміє це зараз — отримає перевагу.\n\n"
            "Стежиш за RWA сектором?"
        ),
        (
            "Liquid Staking: як заробляти поки тримаєш ETH\n\n"
            "Звичайний стейкінг ETH — блокуєш монети і чекаєш.\n"
            "Liquid staking — отримуєш stETH і продовжуєш використовувати капітал.\n\n"
            "→ Lido: $30B+ застейкано, лідер ринку\n"
            "→ EigenLayer: рестейкінг — yield на yield\n"
            "→ 3-5% річних просто за холдинг ETH\n\n"
            "💡 Твої монети можуть працювати поки ти спиш. DeFi це дозволяє.\n\n"
            "Вже використовуєш liquid staking?"
        ),
    ]

    insight = random.choice(INSIGHTS)
    text = (
        f"🏦 <b>{insight.split(chr(10))[0]}</b>\n\n"
        f"{chr(10).join(insight.split(chr(10))[1:])}\n\n"
        f"#defi #web3 #blockchain #крипто #інвестиції"
    )
    return text


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    state = load_state()

    if not should_post(state):
        print("Social post: skipping, not 3 days yet.")
        return

    topic_index = state.get("topic_index", 0) % len(TOPICS)
    topic = TOPICS[topic_index]
    print(f"=== Social post: topic={topic} ===")

    if topic == "crypto":
        text = make_crypto_post()
    elif topic == "stocks":
        text = make_stocks_post()
    else:
        text = make_defi_post()

    send_post(text, topic)

    today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
    state["last_post_date"] = today
    state["topic_index"] = (topic_index + 1) % len(TOPICS)
    save_state(state)

    print(f"Social post sent: {topic}")


if __name__ == "__main__":
    main()
