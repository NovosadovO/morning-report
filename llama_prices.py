# -*- coding: utf-8 -*-
"""llama_prices.py — крипто-ціни ВИКЛЮЧНО з DefiLlama (coins.llama.fi).

Олег попросив: справжня інформація про крипто-активи тільки з defillama.com.
Цей модуль — джерело цін для нових функцій-ініціатив (форсайт), окремо від
CoinGecko, який лишається зашитий у старих звітах (monitor.py, openmind.py
тощо) — той обсяг цим модулем не чіпаємо.

Дані НЕ вигадуються: якщо DefiLlama не відповів — повертаємо {} / None,
і той, хто викликає, тихо мовчить.
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


if __name__ == "__main__":
    print(json.dumps(change_pct(24), indent=2))
