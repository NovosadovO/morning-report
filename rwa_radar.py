#!/usr/bin/env python3
"""
RWA-РАДАР ТОП-10  (Крипто #новий)

Окремий трек по секторі Real World Assets — бо ONDO у Олега в портфелі,
а загальний крипто-блок цього не показує.

Джерела (обидва безкоштовні, без ключів):
  • CoinGecko category=real-world-assets-rwa  → топ-10 монет, ціна, 24h, 7d, mcap
  • CoinGecko /coins/categories               → капіталізація всього сектора
  • DeFiLlama /protocols (category=RWA)       → TVL реальних протоколів

Що робить:
  1. report_block() — блок у щоденний звіт: топ-10 + сектор + TVL.
  2. check_moves()  — ОКРЕМЕ сповіщення, коли є справжня причина:
        · монета з топ-10 зробила ±8% за 24г
        · сектор рухнув ±5% за 24г
        · ONDO зробив ±5% (особистий інтерес)
        · нова монета зайшла в топ-10 (порівняння зі снапшотом)
     До сповіщення AI дає короткий коментар з інтернетом (grounding).
  3. full_text() — /rwa на вимогу.

Нічого не вигадуємо: якщо API не відповів — модуль молчить (і в звіті
пише, що дані недоступні, а не малює нулі).

Callback-префікси: rw_top_ / rw_mute_ / rw_note_
"""

import json
import urllib.request
from datetime import datetime, timedelta

import ai_kit as K

TAG = "rwa"

SNAP_FILE = "rwa_snap.json"        # останній снапшот (для порівняння)
SENT_FILE = "rwa_sent.json"        # антидубль сповіщень
SCAN_STATE = "rwa_scan.json"       # rate-limit перевірки рухів
MUTE_FILE = "rwa_mute.json"        # «сьогодні не турбувати»

SCAN_MIN_GAP_MIN = 90              # перевіряти рухи не частіше ніж раз на 1.5г
TOP_N = 10

MOVE_COIN = 8.0                    # % за 24г для монети з топ-10
MOVE_SECTOR = 5.0                  # % за 24г для всього сектора
MOVE_MINE = 5.0                    # % для ONDO (мій інтерес — нижчий порог)
DEPEG_DROP = 1.0                   # падіння токенізованого фонду за 24г = аварія
DEPEG_JUMP = 2.5                   # ривок угору теж ненормальний
MIN_MCAP_NEW = 3e8                 # нову монету в топ-10 показуємо від $300M

MY_COINS = ("ondo", "ondo-finance")

CG = "https://api.coingecko.com/api/v3"
LLAMA = "https://api.llama.fi/protocols"

_dedup = K.Dedup(SENT_FILE, ttl_days=2)


# ─── ДАНІ ────────────────────────────────────────────────────────────────────

def _get(url, ttl=600):
    """Через кеш monitor (спільний rate-limit), з fallback на прямий запит."""
    try:
        import monitor as _m
        data = _m.fetch_json_cached(url, ttl=ttl)
        if data is not None:
            return data
    except Exception as e:
        K.log(TAG, f"cached fetch failed ({e}) — прямий запит")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "rwa-radar/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        K.log(TAG, f"fetch error [{url[:60]}]: {e}")
        return None


def top_coins(n=TOP_N):
    """Топ-N монет сектора RWA за капіталізацією. [] якщо API недоступний."""
    url = (f"{CG}/coins/markets?vs_currency=usd&category=real-world-assets-rwa"
           f"&order=market_cap_desc&per_page={n}&page=1"
           f"&price_change_percentage=24h,7d")
    raw = _get(url)
    if not isinstance(raw, list):
        return []
    out = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        out.append({
            "id": str(c.get("id") or ""),
            "sym": str(c.get("symbol") or "").upper(),
            "name": str(c.get("name") or ""),
            "price": c.get("current_price"),
            "ch24": c.get("price_change_percentage_24h"),
            "ch7d": c.get("price_change_percentage_7d_in_currency"),
            "mcap": c.get("market_cap"),
        })
    return out


def sector():
    """Капіталізація сектора RWA і зміна за 24г. {} якщо недоступно."""
    raw = _get(f"{CG}/coins/categories", ttl=1800)
    if not isinstance(raw, list):
        return {}
    for c in raw:
        if isinstance(c, dict) and c.get("id") == "real-world-assets-rwa":
            return {"mcap": c.get("market_cap"),
                    "ch24": c.get("market_cap_change_24h"),
                    "vol": c.get("volume_24h")}
    return {}


def tvl_top(n=5):
    """Топ-N RWA-протоколів за TVL + сумарний TVL сектора (DeFiLlama)."""
    raw = _get(LLAMA, ttl=3600)
    if not isinstance(raw, list):
        return [], 0.0
    rwa = [p for p in raw
           if isinstance(p, dict) and str(p.get("category") or "") == "RWA"]
    rwa.sort(key=lambda p: -(p.get("tvl") or 0))
    total = sum((p.get("tvl") or 0) for p in rwa)
    out = [{"name": str(p.get("name") or ""), "tvl": p.get("tvl") or 0,
            "ch7d": p.get("change_7d")} for p in rwa[:n]]
    return out, total


# ─── ФОРМАТУВАННЯ ────────────────────────────────────────────────────────────

def _money(v):
    try:
        v = float(v)
    except Exception:
        return "?"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.1f}K"
    return f"${v:.2f}"


def _price(v):
    try:
        v = float(v)
    except Exception:
        return "?"
    if v >= 100:
        return f"${v:,.0f}".replace(",", " ")
    if v >= 1:
        return f"${v:.3f}"
    return f"${v:.5f}"


def _pct(v):
    try:
        v = float(v)
    except Exception:
        return "—"
    arrow = "🟢" if v > 0 else ("🔴" if v < 0 else "⚪")
    return f"{arrow} {v:+.1f}%"


def _is_mine(coin) -> bool:
    return coin.get("id") in MY_COINS or coin.get("sym") == "ONDO"


_PEG_HINT = ("usd", "usdy", "usyc", "buidl", "eur", "treasur", "money market",
             "institutional digital liquidity", "yield fund", "t-bill", "tbill")


def _pegged(coin) -> bool:
    """Токенізовані фонди/стейбли: ціна тримається біля 1 і майже не рухається.
    Для них ±8% — не «ріст», а ДЕПЕГ, тому логіка сповіщень інша."""
    blob = f"{coin.get('sym', '')} {coin.get('name', '')}".lower()
    try:
        p = float(coin.get("price"))
    except Exception:
        return False
    if not (0.85 <= p <= 1.6):
        return False          # золото, ONDO, LINK — не фонди
    try:
        quiet_week = abs(float(coin.get("ch7d") or 0)) < 2.0
    except Exception:
        quiet_week = False
    # accrual-токени (USDY, USYC, EURSAFO) коштують >1, бо накопичують дохід,
    # тому дивимось не на відрив від 1.00, а на те, що вони СТОЯТЬ на місці
    return quiet_week or any(h in blob for h in _PEG_HINT)


# ─── СНАПШОТ (для порівняння «що змінилось») ──────────────────────────────────

def _save_snap(coins, sec):
    K.save(SNAP_FILE, {
        "ts": K.now().strftime("%Y-%m-%d %H:%M"),
        "ids": [c["id"] for c in coins],
        "prices": {c["id"]: c.get("price") for c in coins},
        "sector_mcap": sec.get("mcap"),
    })


def _load_snap():
    return K.load(SNAP_FILE, default={}) or {}


# ─── БЛОК У ЗВІТ ─────────────────────────────────────────────────────────────

def report_block(n=TOP_N) -> str:
    """Компактний блок для щоденного звіту. '' якщо даних немає."""
    coins = top_coins(n)
    if not coins:
        return "🏦 <b>RWA ТОП-10</b>\n<i>CoinGecko не відповів — даних за зараз немає.</i>"

    sec = sector()
    lines = [f"🏦 <b>RWA ТОП-{len(coins)}</b>"]
    if sec.get("mcap"):
        lines.append(f"Сектор: {_money(sec['mcap'])}  {_pct(sec.get('ch24'))} за 24г")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    for i, c in enumerate(coins, 1):
        star = " ⭐" if _is_mine(c) else ""
        d7 = ""
        if c.get("ch7d") is not None:
            try:
                d7 = f"  <i>7д {float(c['ch7d']):+.1f}%</i>"
            except Exception:
                d7 = ""
        lines.append(f"{i}. <b>{K.esc(c['sym'])}</b>{star} {_price(c['price'])}  "
                     f"{_pct(c.get('ch24'))}{d7}")

    prot, total = tvl_top(3)
    if prot:
        lines.append("")
        lines.append(f"💠 TVL сектора: <b>{_money(total)}</b>")
        for p in prot:
            ch = ""
            if p.get("ch7d") is not None:
                try:
                    ch = f"  ({float(p['ch7d']):+.1f}% 7д)"
                except Exception:
                    ch = ""
            lines.append(f"   • {K.esc(p['name'])} — {_money(p['tvl'])}{ch}")

    _save_snap(coins, sec)
    return "\n".join(lines)


# ─── AI-КОМЕНТАР ─────────────────────────────────────────────────────────────

_AI_PROMPT = """Ти — крипто-аналітик Олега (Кошице). У нього в портфелі ONDO,
тому сектор RWA (real world assets) для нього особистий, не абстрактний.

ЖИВІ ДАНІ ЗАРАЗ ({now}):
{data}

ПРИЧИНА, ЧОМУ ПИШЕШ: {reason}

Дай коментар українською, {size}:
1. Що саме сталося (спирайся ТІЛЬКИ на цифри вище, не вигадуй нових).
2. Чому це могло статися — якщо знаєш реальну новину, назви її; якщо не
   знаєш, чесно скажи "конкретної новини не бачу" і не придумуй.
3. Що це означає саме для ONDO і для позиції Олега.
4. Одна конкретна дія або спостереження на найближчі дні.

Без води, без "інвестуйте обережно". Живою мовою, з емодзі в підзаголовках.
Не обіцяй прибутку і не давай фінансових гарантій."""


def _ai_comment(coins, sec, reason, size="6-9 речень") -> str:
    if not coins:
        return ""
    rows = []
    for c in coins:
        rows.append(f"{c['sym']} {_price(c['price'])} 24г={c.get('ch24')} "
                    f"7д={c.get('ch7d')} mcap={_money(c.get('mcap'))}")
    if sec.get("mcap"):
        rows.append(f"СЕКТОР RWA mcap={_money(sec['mcap'])} 24г={sec.get('ch24')}")
    prot, total = tvl_top(4)
    if prot:
        rows.append("TVL сектора=" + _money(total))
        for p in prot:
            rows.append(f"TVL {p['name']}={_money(p['tvl'])} 7д={p.get('ch7d')}")
    prompt = _AI_PROMPT.format(now=K.now().strftime("%Y-%m-%d %H:%M"),
                              data="\n".join(rows)[:3000],
                              reason=reason, size=size)
    # tag з префіксом MSG_ → grounding.py вмикає пошук в інтернеті
    return K.gemini_text(prompt, max_tokens=2200, temperature=0.6, tag="MSG_RWA")


# ─── РУХИ І СПОВІЩЕННЯ ───────────────────────────────────────────────────────

def _muted() -> bool:
    m = K.load(MUTE_FILE, default={}) or {}
    return m.get("day") == K.today_str()


def mute_today():
    K.save(MUTE_FILE, {"day": K.today_str()})


def _reasons(coins, sec, snap):
    """Список справжніх причин написати. [] якщо нічого не сталося."""
    out = []
    old_ids = set(snap.get("ids") or [])

    for c in coins:
        # Токенізовані фонди: важливий не «ріст», а відрив від 1.00 (депег)
        if _pegged(c) and not _is_mine(c):
            try:
                d24 = float(c.get("ch24"))
            except Exception:
                continue
            # Токенізований фонд не має рухатись. Мінус = проблема з активом,
            # різкий плюс = теж аномалія (ліквідність/помилка ціни).
            if d24 <= -DEPEG_DROP or d24 >= DEPEG_JUMP:
                out.append({
                    "key": f"depeg:{c['id']}:{int(d24 * 10)}",
                    "text": f"🚨 АНОМАЛІЯ: токенізований фонд {c['sym']} "
                            f"({c['name']}) зробив {d24:+.2f}% за 24 години — "
                            f"такі інструменти мають стояти на місці "
                            f"(зараз {_price(c['price'])})",
                })
            continue
        ch = c.get("ch24")
        try:
            ch = float(ch)
        except Exception:
            continue
        limit = MOVE_MINE if _is_mine(c) else MOVE_COIN
        if abs(ch) >= limit:
            word = "виріс" if ch > 0 else "впав"
            mine = " (твоя позиція)" if _is_mine(c) else ""
            out.append({
                "key": f"coin:{c['id']}:{int(ch)}",
                "text": f"{c['sym']}{mine} {word} на {abs(ch):.1f}% за 24 години "
                        f"(зараз {_price(c['price'])})",
            })

    ch_s = sec.get("ch24")
    try:
        ch_s = float(ch_s)
        if abs(ch_s) >= MOVE_SECTOR:
            word = "додав" if ch_s > 0 else "втратив"
            out.append({
                "key": f"sector:{int(ch_s)}",
                "text": f"весь сектор RWA {word} {abs(ch_s):.1f}% за 24 години "
                        f"(капіталізація {_money(sec.get('mcap'))})",
            })
    except Exception:
        pass

    if old_ids:
        new = [c for c in coins if c["id"] not in old_ids
               and not _pegged(c)
               and (c.get("mcap") or 0) >= MIN_MCAP_NEW]
        for c in new:
            out.append({
                "key": f"new:{c['id']}",
                "text": f"нова монета в топ-10 сектора: {c['sym']} ({c['name']}), "
                        f"капіталізація {_money(c.get('mcap'))}",
            })
    return out


def check_moves(force: bool = False) -> int:
    """Сповіщає, тільки коли є справжня причина. Повертає кількість карточок."""
    if not force:
        if _muted():
            return 0
        if not K.rate_ok(SCAN_STATE, SCAN_MIN_GAP_MIN):
            return 0
        K.rate_mark(SCAN_STATE)

    coins = top_coins()
    if not coins:
        K.log(TAG, "API недоступний — молчу (не вигадую)")
        return 0
    sec = sector()
    snap = _load_snap()
    reasons = _reasons(coins, sec, snap)
    _save_snap(coins, sec)

    if not reasons:
        K.log(TAG, "рухів понад порог немає")
        return 0

    fresh = [r for r in reasons if not _dedup.seen("rwa", r["key"])]
    if not fresh:
        K.log(TAG, f"{len(reasons)} причин, але всі вже надіслані")
        return 0

    head_lines = [f"• {r['text']}" for r in fresh[:4]]
    reason_txt = "; ".join(r["text"] for r in fresh[:4])
    ai = _ai_comment(coins, sec, reason_txt, size="6-9 речень")

    text = ("🏦 <b>RWA-РАДАР</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(K.esc(x) for x in head_lines))
    if ai:
        text += "\n\n" + ai
    else:
        text += "\n\n<i>AI-коментар недоступний — вище тільки живі цифри.</i>"

    kb = [
        [{"text": "📊 Весь топ-10", "callback_data": "rw_top_now"}],
        [{"text": "📝 В нотатки", "callback_data": "rw_note_now"},
         {"text": "🔕 Сьогодні не турбувати", "callback_data": "rw_mute_today"}],
    ]
    ok = K.send_card(text[:3900], kb, tag=TAG)
    if ok:
        for r in fresh:
            _dedup.mark("rwa", r["key"])
        K.log(TAG, f"✅ сповіщення: {len(fresh)} причин")
        try:
            import response_log
            response_log.log_response("rwa_move", "RWA", reason_txt[:120], {})
        except Exception:
            pass
    return 1 if ok else 0


# ─── /rwa ────────────────────────────────────────────────────────────────────

def full_text(with_ai: bool = True) -> str:
    block = report_block()
    if not with_ai:
        return block
    coins = top_coins()
    sec = sector()
    ai = _ai_comment(coins, sec, "Олег сам запитав огляд сектора RWA",
                     size="8-12 речень")
    return block + (("\n\n" + ai) if ai else "")


def note_text() -> str:
    """Короткий рядок для ai_notes."""
    coins = top_coins(5)
    if not coins:
        return ""
    parts = [f"{c['sym']} {_price(c['price'])} ({c.get('ch24') or 0:+.1f}%)"
             for c in coins]
    return "RWA топ-5: " + ", ".join(parts)


if __name__ == "__main__":
    import sys
    if "--moves" in sys.argv:
        print("карточок:", check_moves(force=True))
    elif "--ai" in sys.argv:
        print(full_text())
    else:
        print(report_block())
