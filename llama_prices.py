# -*- coding: utf-8 -*-
"""llama_prices.py — крипто-ціни: DefiLlama (coins.llama.fi) основне джерело.

Олег попросив: вся крипто-інформація (ціни/24h/7d/30d/графіки) — з DefiLlama.
CoinGecko лишається лише як fallback (за явним дозволом Олега — "що бракує,
хай бере з coingecko"), і тільки коли DefiLlama сам не відповів або не має
конкретної монети.

Цей модуль тепер джерело цін і для форсайту (change_pct/current_prices —
старий вузький API, backward-compat), і для watchlist/портфелю/топ-20 у
context.py, monitor.py, message_generator.py, monthly_coach.py, weekly_coach.py,
weekly_review.py, smart_notifications*.py, intelligent_listener.py,
intelligent_assistant_v2.py, portfolio.py, charts.py (get_snapshot/
to_simple_price_shape/to_markets_shape — новий генеральний API).

Рейтинги за капіталізацією (топ-100 динамічно), трендові монети, новини,
категорії (RWA) — DefiLlama цього не дає взагалі, там CoinGecko лишається
основним джерелом (openmind.py, rwa_radar.py, monitor.check_crypto_news) —
той обсяг цим модулем не чіпаємо, за згодою Олега.

Дані НЕ вигадуються: якщо і DefiLlama, і CoinGecko-fallback не відповіли —
повертаємо {} / None, і той, хто викликає, тихо мовчить.
"""

import json
import time
import urllib.request
import urllib.error

TAG = "llama_prices"

BASE = "https://coins.llama.fi"
UA = {"User-Agent": "Mozilla/5.0"}

# Watchlist Олега (той самий, що вже фігурує в пам'яті/боті)
CG_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "AVAX": "avalanche-2",
    "ONDO": "ondo-finance", "SOL": "solana", "BNB": "binancecoin",
    "XRP": "ripple", "DOGE": "dogecoin",
}

_CACHE_TTL = 180  # 3 хв — щоб не бити API щохвилини з watcher-циклу
_cache = {}  # {url: (ts, data)}


def _log(m):
    try:
        import ai_kit as K
        K.log(TAG, m)
    except Exception:
        print(f"[{TAG}] {m}", flush=True)


def _get_json(url: str, timeout: int = 12):
    now = time.time()
    hit = _cache.get(url)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        _cache[url] = (now, data)
        return data
    except Exception as e:
        _log("GET " + url.split("?")[0] + " error: " + str(e))
        return None


def _coin_ids(symbols) -> str:
    ids = [CG_IDS[s] for s in symbols if s in CG_IDS]
    return ",".join("coingecko:" + i for i in ids)


def current_prices(symbols=None) -> dict:
    """{'BTC': {'price': 123.4, 'ts': 1234567890}, ...} — тільки ті, що знайшлись."""
    symbols = symbols or list(CG_IDS.keys())
    key = _coin_ids(symbols)
    if not key:
        return {}
    data = _get_json(BASE + "/prices/current/" + key)
    coins = (data or {}).get("coins") if isinstance(data, dict) else None
    if not isinstance(coins, dict):
        return {}
    out = {}
    for sym, cg_id in CG_IDS.items():
        if sym not in symbols:
            continue
        row = coins.get("coingecko:" + cg_id)
        if isinstance(row, dict) and row.get("price") is not None:
            out[sym] = {"price": row["price"], "ts": row.get("timestamp")}
    return out


def historical_prices(unix_ts: int, symbols=None) -> dict:
    """Ціна на конкретний момент у минулому. Той самий формат, що current_prices."""
    symbols = symbols or list(CG_IDS.keys())
    key = _coin_ids(symbols)
    if not key:
        return {}
    data = _get_json(BASE + "/prices/historical/" + str(int(unix_ts)) + "/" + key)
    coins = (data or {}).get("coins") if isinstance(data, dict) else None
    if not isinstance(coins, dict):
        return {}
    out = {}
    for sym, cg_id in CG_IDS.items():
        if sym not in symbols:
            continue
        row = coins.get("coingecko:" + cg_id)
        if isinstance(row, dict) and row.get("price") is not None:
            out[sym] = {"price": row["price"], "ts": row.get("timestamp")}
    return out


def change_pct(hours: int, symbols=None) -> dict:
    """{'BTC': {'now': X, 'then': Y, 'pct': Z}, ...} — тільки монети з обома точками."""
    symbols = symbols or list(CG_IDS.keys())
    now_p = current_prices(symbols)
    if not now_p:
        return {}
    then_ts = int(time.time()) - int(hours) * 3600
    then_p = historical_prices(then_ts, symbols)
    if not then_p:
        return {}
    out = {}
    for sym in now_p:
        if sym not in then_p:
            continue
        now_v = now_p[sym]["price"]
        then_v = then_p[sym]["price"]
        if not then_v:
            continue
        pct = round((now_v - then_v) / then_v * 100, 2)
        out[sym] = {"now": now_v, "then": then_v, "pct": pct}
    return out


# ─── ЗАГАЛЬНИЙ API (довільний symbol→coingecko_id, для watchlist/портфелю/топ-20) ──
# Олег уточнив (2026): вся крипто-інформація — DefiLlama основне джерело,
# CoinGecko — тільки fallback там, де DefiLlama не відповів або не має даних
# (рейтинги за капіталізацією, трендові монети, новини — тих полей у DefiLlama нема).

BASE_CG = "https://api.coingecko.com/api/v3"
_PERIOD_KEYS = {"24h": "change_24h", "7d": "change_7d", "30d": "change_30d"}


def _generic_coin_ids(id_map: dict, symbols) -> str:
    ids = [id_map[s] for s in symbols if s in id_map]
    return ",".join("coingecko:" + i for i in ids)


def _percentage(id_map: dict, symbols, period: str) -> dict:
    """{'BTC': -1.23, ...} — % зміна за period ('24h'/'7d'/'30d') напряму з DefiLlama."""
    key = _generic_coin_ids(id_map, symbols)
    if not key:
        return {}
    data = _get_json(BASE + "/percentage/" + key + "?period=" + period)
    coins = (data or {}).get("coins") if isinstance(data, dict) else None
    if not isinstance(coins, dict):
        return {}
    out = {}
    for sym in symbols:
        cg_id = id_map.get(sym)
        if not cg_id:
            continue
        val = coins.get("coingecko:" + cg_id)
        if isinstance(val, (int, float)):
            out[sym] = round(val, 2)
    return out


def _current_prices_generic(id_map: dict, symbols) -> dict:
    key = _generic_coin_ids(id_map, symbols)
    if not key:
        return {}
    data = _get_json(BASE + "/prices/current/" + key)
    coins = (data or {}).get("coins") if isinstance(data, dict) else None
    if not isinstance(coins, dict):
        return {}
    out = {}
    for sym in symbols:
        cg_id = id_map.get(sym)
        if not cg_id:
            continue
        row = coins.get("coingecko:" + cg_id)
        if isinstance(row, dict) and row.get("price") is not None:
            out[sym] = {"price": row["price"], "ts": row.get("timestamp")}
    return out


def _coingecko_fallback(id_map: dict, symbols, periods) -> dict:
    """Fallback на CoinGecko simple/price — лише для symbols, яких DefiLlama не дав.
    Викликається тільки якщо в них не вистачає даних (за згодою Олега)."""
    ids = [id_map[s] for s in symbols if s in id_map]
    if not ids:
        return {}
    change_params = []
    if "24h" in periods:
        change_params.append("include_24hr_change=true")
    if "7d" in periods:
        change_params.append("include_7d_change=true")
    if "30d" in periods:
        change_params.append("include_30d_change=true")
    url = (BASE_CG + "/simple/price?ids=" + ",".join(ids) + "&vs_currencies=usd&"
           + "&".join(change_params))
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
    except Exception as e:
        _log("coingecko fallback error: " + str(e))
        return {}
    out = {}
    rev = {v: k for k, v in id_map.items()}
    for cg_id, row in (data or {}).items():
        sym = rev.get(cg_id)
        if not sym or not isinstance(row, dict) or row.get("usd") is None:
            continue
        entry = {"price": row["usd"], "ts": int(time.time()), "source": "coingecko"}
        if "24h" in periods and row.get("usd_24h_change") is not None:
            entry["change_24h"] = round(row["usd_24h_change"], 2)
        if "7d" in periods and row.get("usd_7d_change") is not None:
            entry["change_7d"] = round(row["usd_7d_change"], 2)
        if "30d" in periods and row.get("usd_30d_change") is not None:
            entry["change_30d"] = round(row["usd_30d_change"], 2)
        out[sym] = entry
    return out


def get_snapshot(id_map: dict, symbols=None, periods=("24h",)) -> dict:
    """Універсальна функція: ціна + % зміни для довільного набору монет.

    id_map: {"BTC": "bitcoin", ...} — символ → coingecko id.
    symbols: список символів (None = всі з id_map).
    periods: ("24h",), ("7d",), ("24h","7d"), ("30d",) тощо.

    Повертає {"BTC": {"price":.., "change_24h":.., "change_7d":.., "ts":.., "source":"defillama"}, ...}.
    Джерело — DefiLlama. Для монет, яких DefiLlama не знайшов, тихо пробуємо
    CoinGecko (fallback, за явним дозволом Олега — "що бракує, хай бере з coingecko").
    """
    symbols = list(symbols) if symbols else list(id_map.keys())
    prices = _current_prices_generic(id_map, symbols)

    out = {}
    for sym, p in prices.items():
        out[sym] = {"price": p["price"], "ts": p.get("ts"), "source": "defillama"}

    for period in periods:
        pct_map = _percentage(id_map, list(out.keys()), period)
        key = _PERIOD_KEYS.get(period, "change_" + period)
        for sym, pct in pct_map.items():
            if sym in out:
                out[sym][key] = pct

    missing = [s for s in symbols if s not in out]
    if missing:
        fb = _coingecko_fallback(id_map, missing, periods)
        out.update(fb)

    return out


# ─── Адаптери під старий формат CoinGecko (щоб не переписувати downstream-логіку) ──

def to_simple_price_shape(id_map: dict, symbols=None, periods=("24h",)) -> dict:
    """Емулює формат CoinGecko /simple/price: {cg_id: {"usd":.., "usd_24h_change":.., "usd_7d_change":..}}."""
    snap = get_snapshot(id_map, symbols, periods)
    out = {}
    for sym, row in snap.items():
        cg_id = id_map.get(sym)
        if not cg_id:
            continue
        entry = {"usd": row.get("price")}
        if "change_24h" in row:
            entry["usd_24h_change"] = row["change_24h"]
        if "change_7d" in row:
            entry["usd_7d_change"] = row["change_7d"]
        if "change_30d" in row:
            entry["usd_30d_change"] = row["change_30d"]
        out[cg_id] = entry
    return out


def to_markets_shape(id_map: dict, symbols=None, periods=("24h",)) -> dict:
    """Емулює формат CoinGecko /coins/markets, ключ — cg_id (як у монітора вже
    прийнято робити `{c["id"]: c for c in raw}`): {cg_id: {"id":, "symbol":,
    "current_price":, "price_change_percentage_24h":,
    "price_change_percentage_7d_in_currency":, "price_change_percentage_30d_in_currency":}}."""
    snap = get_snapshot(id_map, symbols, periods)
    out = {}
    for sym, row in snap.items():
        cg_id = id_map.get(sym)
        if not cg_id:
            continue
        entry = {
            "id": cg_id, "symbol": sym.lower(),
            "current_price": row.get("price"),
        }
        if "change_24h" in row:
            entry["price_change_percentage_24h"] = row["change_24h"]
        if "change_7d" in row:
            entry["price_change_percentage_7d_in_currency"] = row["change_7d"]
        if "change_30d" in row:
            entry["price_change_percentage_30d_in_currency"] = row["change_30d"]
        out[cg_id] = entry
    return out


if __name__ == "__main__":
    print(json.dumps(change_pct(24), indent=2))
