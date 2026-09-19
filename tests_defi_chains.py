"""Офлайн-тест report_defi: get_chains_7d_change() рахує TVL з
historicalChainTvl (без double-count), а не з "надутого" /chains, і RWA-блоки
несуть дисклеймер про методологію."""
import os, sys

os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import report_defi as rd

fails = []


def chk(cond, name):
    if not cond:
        fails.append(name)
        print(f"❌ {name}")
    else:
        print(f"✅ {name}")


# ── Мок _get: /chains дає "надуте" TVL, historicalChainTvl — точне ──
_CHAINS_LIST = [
    {"name": "Ethereum", "tvl": 115_000_000_000},  # надуте (з double-count)
    {"name": "Solana", "tvl": 14_000_000_000},
    {"name": "Base", "tvl": 9_000_000_000},
]

_HIST = {
    "Ethereum": [{"date": i, "tvl": 50_000_000_000 + i * 1000} for i in range(20)],
    "Solana":   [{"date": i, "tvl": 6_000_000_000 + i * 500} for i in range(20)],
    "Base":     [{"date": i, "tvl": 5_900_000_000 + i * 200} for i in range(20)],
}


def fake_get(url, retries=3):
    if url.endswith("/chains"):
        return _CHAINS_LIST
    for name, hist in _HIST.items():
        if f"historicalChainTvl/{name}" in url:
            return hist
    return None


rd._get = fake_get

block = rd.get_chains_7d_change(top_n=3)
chk(block is not None, "get_chains_7d_change повертає блок")
chk("Ethereum" in block, "блок містить Ethereum")
# Головна перевірка: TVL в блоці має братись з historicalChainTvl (~50B), а
# НЕ з надутого /chains (115B) — інакше повернеться регресія з double-count.
chk("$50." in block or "$50" in block, "TVL береться з historicalChainTvl, а не з надутого /chains")
chk("115" not in block, "надуте TVL з /chains НЕ потрапляє в звіт")
chk("7д:" in block, "є 7-денна зміна")

# ── RWA-блок містить дисклеймер про методологію defillama.com/rwa ──
protocols = [
    {"name": "Ondo", "category": "RWA", "tvl": 500_000_000, "change_1d": 1.2,
     "change_7d": 3.4, "chains": ["Ethereum"]},
]
rwa_block = rd.get_rwa(protocols)
chk("RWA-протоколи (DeFi TVL)" in rwa_block, "RWA-блок перейменований (не 'RWA ринок')")
chk("безкоштовне API його не дає" in rwa_block or "defillama.com/rwa" in rwa_block,
    "RWA-блок містить дисклеймер про іншу методологію сайту")

print()
if fails:
    print(f"ПРОВАЛЕНО: {len(fails)} — {fails}")
    sys.exit(1)
else:
    print("Усі тести пройшли ✅")
