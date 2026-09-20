"""Офлайн-тест report_etf: парсинг SoSoValue-подібної відповіді, форматування
блоків (сьогодні/7д/AUM), компактний блок для вбудовування у DeFi-звіт."""
import os

os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import report_etf as re

fails = []


def chk(cond, name):
    if not cond:
        fails.append(name)
        print(f"❌ {name}")
    else:
        print(f"✅ {name}")


# ── Мок даних, як віддає SoSoValue /etfs/summary-history (найновіший день першим) ──
_ROWS_BTC = [
    {"date": "2026-09-18", "total_net_inflow": 433027402.35, "total_net_assets": 102531745269.3, "cum_net_inflow": 55160933041.1},
    {"date": "2026-09-17", "total_net_inflow": 159453723.25, "total_net_assets": 96247076017.2, "cum_net_inflow": 54727905638.8},
    {"date": "2026-09-16", "total_net_inflow": -295980763.08, "total_net_assets": 95185081971.9, "cum_net_inflow": 54568451915.5},
    {"date": "2026-09-15", "total_net_inflow": -450329418.71, "total_net_assets": 95716988283.2, "cum_net_inflow": 54864432678.6},
    {"date": "2026-09-14", "total_net_inflow": 160042713.815, "total_net_assets": 100091702141.1, "cum_net_inflow": 55314762097.3},
    {"date": "2026-09-13", "total_net_inflow": 0, "total_net_assets": 100000000000.0, "cum_net_inflow": 55154719383.5},
    {"date": "2026-09-12", "total_net_inflow": 50000000, "total_net_assets": 99900000000.0, "cum_net_inflow": 55154719383.5},
]


def fake_get(path, params, retries=2):
    if params.get("symbol") == "BTC":
        return _ROWS_BTC
    if params.get("symbol") == "ETH":
        return []  # симулюємо тимчасову недоступність ETH-даних
    return None


re._get = fake_get

# ── 1. get_etf_flows повертає рядки як є ──
rows = re.get_etf_flows("BTC", days=7)
chk(rows is not None and len(rows) == 7, "get_etf_flows(BTC) повертає 7 днів")
chk(rows[0]["date"] == "2026-09-18", "найновіший день першим")

# ── 2. format_etf_block рахує суму netflow за 7д правильно ──
block = re.format_etf_block("BTC", "Bitcoin spot ETF", "🟠", rows)
expected_sum7 = sum(r["total_net_inflow"] for r in rows[:7])
chk(re.fmt_usd(expected_sum7) in block, f"7д сума ({re.fmt_usd(expected_sum7)}) присутня у блоці")
chk("$102.53B" in block, "AUM показаний правильно")
chk("🟢" in block and "🔴" in block, "є і зелені, і червоні дні (mixed flow)")

# ── 3. Порожні дані (ETH) не ламають build_report_text, дають попередження в блоці ──
eth_block = re.format_etf_block("ETH", "Ethereum spot ETF", "🔵", None)
chk("недоступні" in eth_block, "порожні дані ETH дають попередження, а не крах")

# ── 4. build_report_text працює навіть коли один символ недоступний (BTC є, ETH немає) ──
full_text = re.build_report_text()
chk(full_text is not None, "build_report_text не None, коли хоча б один символ є")
chk("Bitcoin spot ETF" in full_text, "BTC блок присутній у повному звіті")

# ── 5. compact_block для вбудовування у DeFi-звіт ──
compact = re.compact_block(("BTC", "ETH"))
chk(compact is not None, "compact_block не None")
chk("Bitcoin spot ETF" in compact and "TVL" in compact, "compact_block містить TVL і назву")
chk("Останні 5 днів" not in compact, "compact_block без мінітаблиці (компактний)")

# ── 6. Якщо ВСІ символи недоступні — build_report_text повертає None (немає порожнього спаму) ──
re._get = lambda path, params, retries=2: []
chk(re.build_report_text() is None, "build_report_text повертає None, коли всі дані недоступні")

print()
if fails:
    print(f"FAILED ({len(fails)}): {fails}")
    raise SystemExit(1)
else:
    print("ALL PASSED ✅")
