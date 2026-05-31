"""
Портфель трекер — P&L в реальному часі.
Позиції зберігаються в GitHub (data/portfolio_positions.json).
"""
import os
import json
import requests
from datetime import datetime, timezone, timedelta

# ─── ПОЗИЦІЇ ПОРТФЕЛЮ ────────────────────────────────────────────────────────
# Формат: "SYMBOL": {"amount": кількість, "avg_buy": середня ціна купівлі}
# avg_buy = 0 якщо невідомо (тоді P&L не рахується, тільки поточна вартість)

DEFAULT_POSITIONS = {
    "ETH":   {"amount": 0.8831,    "avg_buy": 0,   "coingecko": "ethereum"},
    "AVAX":  {"amount": 128.08,    "avg_buy": 0,   "coingecko": "avalanche-2"},
    "USDC":  {"amount": 5.201,     "avg_buy": 1.0, "coingecko": "usd-coin"},
    "ONDO":  {"amount": 1294.66,   "avg_buy": 0,   "coingecko": "ondo-finance"},
    "AAVE":  {"amount": 4.587,     "avg_buy": 0,   "coingecko": "aave"},
    "BTC":   {"amount": 0.0042,    "avg_buy": 0,   "coingecko": "bitcoin"},
    "LINK":  {"amount": 33.05,     "avg_buy": 0,   "coingecko": "chainlink"},
    "BNB":   {"amount": 0.1655,    "avg_buy": 0,   "coingecko": "binancecoin"},
    "NEAR":  {"amount": 22.91,     "avg_buy": 0,   "coingecko": "near"},
    "ARB":   {"amount": 212.41,    "avg_buy": 0,   "coingecko": "arbitrum"},
    "LINEA": {"amount": 14565.43,  "avg_buy": 0,   "coingecko": "linea"},
    "CRO":   {"amount": 256.84,    "avg_buy": 0,   "coingecko": "crypto-com-chain"},
    "UNI":   {"amount": 4.584,     "avg_buy": 0,   "coingecko": "uniswap"},
    "ID":    {"amount": 340.68,    "avg_buy": 0,   "coingecko": "space-id"},
    "G":     {"amount": 2612.40,   "avg_buy": 0,   "coingecko": "gravity-alpha"},
    "ADA":   {"amount": 28.44,     "avg_buy": 0,   "coingecko": "cardano"},
    "SWEAT": {"amount": 6131.94,   "avg_buy": 0,   "coingecko": "sweat-economy"},
    # aBnbWBNB — DeFi LP, немає ціни на CoinGecko, пропускаємо
    # COQ, MEME — кількість не видна на скрін (обрізано)
}

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO  = os.getenv("GITHUB_REPO", "NovosadovO/morning-report")
POSITIONS_FILE = "portfolio_positions.json"


def _gh_load(filename):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return None, None
    try:
        import base64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/{filename}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            content = base64.b64decode(data["content"]).decode()
            return json.loads(content), data["sha"]
        return None, None
    except Exception:
        return None, None


def _gh_save(filename, data, sha=None):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False
    try:
        import base64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/{filename}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Content-Type": "application/json"}
        content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
        payload = {"message": f"update {filename}", "content": content, "branch": "data"}
        if sha:
            payload["sha"] = sha
        r = requests.put(url, headers=headers, json=payload, timeout=10)
        return r.status_code in (200, 201)
    except Exception:
        return False


def load_positions():
    """Завантажує позиції з GitHub або повертає дефолтні."""
    data, _ = _gh_load(POSITIONS_FILE)
    if data:
        return data
    return DEFAULT_POSITIONS.copy()


def save_positions(positions):
    _, sha = _gh_load(POSITIONS_FILE)
    _gh_save(POSITIONS_FILE, positions, sha)


def get_prices_coingecko(coin_ids: list) -> dict:
    """Отримує ціни з CoinGecko для списку id."""
    try:
        ids_str = ",".join(coin_ids)
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids_str}&vs_currencies=usd&include_24hr_change=true"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"CoinGecko error: {e}")
        return {}


def get_portfolio_summary() -> dict:
    """
    Повертає dict з повним станом портфелю:
    - positions: кожна монета з поточною ціною, вартістю, P&L
    - total_value: загальна вартість в USD
    - total_pnl: загальний P&L (якщо є avg_buy)
    - change_24h_usd: зміна за 24г в USD
    """
    positions = load_positions()

    # Збираємо всі coingecko id
    coin_ids = [v.get("coingecko", k.lower()) for k, v in positions.items() if v.get("amount", 0) > 0]
    prices_data = get_prices_coingecko(coin_ids)

    # Маппінг coingecko_id → price
    price_map = {}
    change_map = {}
    for cg_id, pdata in prices_data.items():
        price_map[cg_id]  = pdata.get("usd", 0)
        change_map[cg_id] = pdata.get("usd_24h_change", 0)

    result = {}
    total_value    = 0.0
    total_cost     = 0.0
    total_change24 = 0.0
    has_pnl        = False

    for sym, pos in positions.items():
        amount  = pos.get("amount", 0)
        avg_buy = pos.get("avg_buy", 0)
        cg_id   = pos.get("coingecko", sym.lower())

        if amount <= 0:
            continue

        price     = price_map.get(cg_id, 0)
        change24  = change_map.get(cg_id, 0)
        value     = price * amount
        change24_usd = value * change24 / 100 if change24 else 0

        pnl     = None
        pnl_pct = None
        if avg_buy > 0 and price > 0:
            cost    = avg_buy * amount
            pnl     = value - cost
            pnl_pct = (price / avg_buy - 1) * 100
            total_cost += cost
            has_pnl = True

        total_value    += value
        total_change24 += change24_usd

        result[sym] = {
            "amount":      amount,
            "price":       price,
            "value":       value,
            "change24":    change24,
            "change24_usd": change24_usd,
            "pnl":         pnl,
            "pnl_pct":     pnl_pct,
            "avg_buy":     avg_buy,
        }

    total_pnl = (total_value - total_cost) if has_pnl and total_cost > 0 else None

    return {
        "positions":    result,
        "total_value":  total_value,
        "total_pnl":    total_pnl,
        "change_24h":   total_change24,
        "has_pnl":      has_pnl,
    }


def format_portfolio_block(short=False) -> str:
    """Форматований блок для Telegram звіту."""
    try:
        summary = get_portfolio_summary()
        positions = summary["positions"]
        total = summary["total_value"]
        pnl   = summary["total_pnl"]
        ch24  = summary["change_24h"]

        if not positions or total == 0:
            return "💼 <b>ПОРТФЕЛЬ</b>\n⚠️ Немає даних"

        # Сортуємо за вартістю
        sorted_pos = sorted(positions.items(), key=lambda x: x[1]["value"], reverse=True)

        ch24_sign = "+" if ch24 >= 0 else ""
        ch24_emoji = "📈" if ch24 >= 0 else "📉"

        lines = [f"💼 <b>ПОРТФЕЛЬ</b>  ${total:,.0f}"]
        lines.append(f"{ch24_emoji} За 24г: <b>{ch24_sign}${ch24:,.0f}</b>")

        if pnl is not None:
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"{pnl_emoji} P&L: <b>{pnl_sign}${pnl:,.0f}</b>")

        lines.append("")

        if short:
            # Короткий режим — тільки топ 5 і загальне
            for sym, pos in sorted_pos[:5]:
                val = pos["value"]
                ch  = pos["change24"]
                pct = val / total * 100
                sign = "+" if ch >= 0 else ""
                lines.append(f"  <b>{sym}</b> ${val:,.0f} ({pct:.0f}%)  {sign}{ch:.1f}%")
            if len(sorted_pos) > 5:
                rest_val = sum(p["value"] for _, p in sorted_pos[5:])
                lines.append(f"  <i>+{len(sorted_pos)-5} інших  ${rest_val:,.0f}</i>")
        else:
            # Повний режим
            for sym, pos in sorted_pos:
                val    = pos["value"]
                ch     = pos["change24"]
                pct    = val / total * 100
                ch_sign = "+" if ch >= 0 else ""
                ch_emoji = "▲" if ch >= 0 else "▼"
                line = f"  <b>{sym}</b>  ${val:,.0f}  ({pct:.0f}%)  {ch_emoji}{abs(ch):.1f}%"
                if pos.get("pnl") is not None:
                    pnl_s = pos["pnl"]
                    p_sign = "+" if pnl_s >= 0 else ""
                    line += f"  P&L: {p_sign}${pnl_s:,.0f}"
                lines.append(line)

        return "\n".join(lines)

    except Exception as e:
        return f"💼 <b>ПОРТФЕЛЬ</b>\n⚠️ Помилка: {e}"


def update_position(symbol: str, amount: float = None, avg_buy: float = None) -> str:
    """Оновлює позицію через команду бота."""
    positions = load_positions()
    sym = symbol.upper()

    # Знаходимо coingecko id
    cg_map = {
        "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
        "AVAX": "avalanche-2", "ONDO": "ondo-finance", "AAVE": "aave",
        "LINK": "chainlink", "NEAR": "near", "ARB": "arbitrum",
        "USDC": "usd-coin", "USDT": "tether", "SOL": "solana",
        "LINEA": "linea", "XRP": "ripple", "DOT": "polkadot",
        "TON": "the-open-network", "SUI": "sui",
        "CRO": "crypto-com-chain", "UNI": "uniswap",
        "ID": "space-id", "G": "gravity-alpha",
        "ADA": "cardano", "SWEAT": "sweat-economy",
        "COQ": "coq-inu", "MEME": "memecoin-2",
    }

    if sym not in positions:
        positions[sym] = {"amount": 0, "avg_buy": 0, "coingecko": cg_map.get(sym, sym.lower())}

    if amount is not None:
        positions[sym]["amount"] = amount
    if avg_buy is not None:
        positions[sym]["avg_buy"] = avg_buy

    save_positions(positions)
    return f"✅ {sym}: {positions[sym]['amount']} монет, avg buy ${positions[sym]['avg_buy']}"


def update_avg_buy(symbol: str, qty: float, price: float) -> str:
    """Записує нову угоду покупки, перераховує середню ціну (weighted average)."""
    positions = load_positions()
    sym = symbol.upper()

    cg_map = {
        "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
        "AVAX": "avalanche-2", "ONDO": "ondo-finance", "AAVE": "aave",
        "LINK": "chainlink", "NEAR": "near", "ARB": "arbitrum",
        "USDC": "usd-coin", "USDT": "tether", "SOL": "solana",
        "LINEA": "linea", "XRP": "ripple", "DOT": "polkadot",
        "TON": "the-open-network", "SUI": "sui",
        "CRO": "crypto-com-chain", "UNI": "uniswap",
        "ID": "space-id", "G": "gravity-alpha",
        "ADA": "cardano", "SWEAT": "sweat-economy",
        "COQ": "coq-inu", "MEME": "memecoin-2",
    }

    if sym not in positions:
        positions[sym] = {"amount": 0, "avg_buy": 0, "coingecko": cg_map.get(sym, sym.lower())}

    cur = positions[sym]
    old_qty = float(cur.get("amount", 0))
    old_avg = float(cur.get("avg_buy", 0))

    if old_qty > 0 and old_avg > 0:
        # Weighted average
        new_avg = (old_qty * old_avg + qty * price) / (old_qty + qty)
    else:
        new_avg = price

    new_qty = old_qty + qty
    positions[sym]["amount"] = round(new_qty, 8)
    positions[sym]["avg_buy"] = round(new_avg, 4)

    save_positions(positions)
    return (f"✅ <b>{sym}</b> оновлено\n"
            f"  Кількість: {new_qty}\n"
            f"  Середня ціна купівлі: ${new_avg:,.2f}")


if __name__ == "__main__":
    print(format_portfolio_block())
