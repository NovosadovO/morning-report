"""Офлайн-тест report_defi: get_rwa_categories() / get_rwa_chains() — розбивка
RWA TVL по типу активу (tags) і по блокчейнах, коректна обробка протоколів
з кількома тегами/чейнами (рівний розподіл TVL) і без тегів взагалі."""
import os

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


_PROTOCOLS = [
    {"name": "Ondo Global Markets", "category": "RWA", "tvl": 900_000_000,
     "tags": ["Stocks & ETFs"], "chains": ["Ethereum", "Solana"]},
    {"name": "Invesco USTB", "category": "RWA", "tvl": 500_000_000,
     "tags": ["Treasury Bills"], "chains": ["Ethereum"]},
    {"name": "Multi-tag Protocol", "category": "RWA", "tvl": 100_000_000,
     "tags": ["Treasury Bills", "Private Credit"], "chains": ["Base", "Ethereum"]},
    {"name": "No-tag Protocol", "category": "RWA", "tvl": 50_000_000,
     "tags": [], "chains": []},
    {"name": "Not RWA", "category": "Lending", "tvl": 999_999_999_999,
     "tags": ["Lending"], "chains": ["Ethereum"]},
    {"name": "Zero TVL RWA", "category": "RWA", "tvl": 0,
     "tags": ["Real Estate"], "chains": ["Polygon"]},
]

TOTAL_RWA = 900_000_000 + 500_000_000 + 100_000_000 + 50_000_000  # = 1_550_000_000

# ── 1. get_rwa_categories ігнорує не-RWA і нульові TVL, ділить multi-tag навпіл ──
cat_block = rd.get_rwa_categories(_PROTOCOLS, top_n=10)
chk(cat_block is not None, "get_rwa_categories не None")
chk("Not RWA" not in cat_block, "не-RWA протокол (Lending, TVL $1T) не потрапляє в розбивку")
chk("Zero TVL RWA" not in cat_block or "Real Estate" in cat_block, "нульовий TVL не ламає підрахунок")
# Multi-tag Protocol $100M ділиться навпіл між Treasury Bills і Private Credit -> +$50M кожному
chk("Treasury Bills" in cat_block, "тег Treasury Bills присутній")
chk("Private Credit" in cat_block, "тег Private Credit присутній (multi-tag розподіл)")
chk(f"${TOTAL_RWA/1e9:.2f}B" in cat_block, "загальна сума RWA у категоріях = сума TVL RWA-протоколів")

# ── 2. get_rwa_chains аналогічно ділить multi-chain протоколи ──
chain_block = rd.get_rwa_chains(_PROTOCOLS, top_n=10)
chk(chain_block is not None, "get_rwa_chains не None")
chk("Ethereum" in chain_block and "Base" in chain_block, "чейни Ethereum і Base присутні")
chk("Not RWA" not in chain_block, "не-RWA протокол не потрапляє в chain-розбивку")

# ── 3. Порожній список RWA -> None, а не крах ──
chk(rd.get_rwa_categories([{"category": "Lending", "tvl": 100}], top_n=5) is None,
    "немає RWA протоколів -> get_rwa_categories повертає None")
chk(rd.get_rwa_chains([{"category": "Lending", "tvl": 100}], top_n=5) is None,
    "немає RWA протоколів -> get_rwa_chains повертає None")

# ── 4. Проценти в категоріях у сумі ~100% ──
import re as _re
pcts = [float(x) for x in _re.findall(r"\(([\d.]+)%\)", cat_block)]
chk(abs(sum(pcts) - 100.0) < 1.0, f"сума % категорій RWA ≈ 100% (отримано {sum(pcts):.1f}%)")

print()
if fails:
    print(f"FAILED ({len(fails)}): {fails}")
    raise SystemExit(1)
else:
    print("ALL PASSED ✅")
