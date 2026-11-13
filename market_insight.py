#!/usr/bin/env python3
"""
market_insight.py — AI-розбір крипти/портфеля з ГРАФІКОМ і питанням-кнопками.

Тема вибирається не за розкладом, а за РЕАЛЬНОЮ подією в цінах (price_history_30d,
DefiLlama): рух за 24 год незвично сильний для цієї монети (z-score проти її
власних 24-год рухів), або нові мін/макс за вікно історії при русі за 7 днів ≥5%.
Монети з портфеля Олега важать більше. Та сама монета не частіше ніж раз на 3
розбори. Подій немає → мовчимо. Дані старші за 5 год → мовчимо.

Gemini бачить тільки ці числа (+ вартість позиції з portfolio, якщо монета у
портфелі) і пише розбір + питання з варіантами, що відповідають питанню.
Нічого не купує/не продає — лише кнопки-відповіді в пам'ять AI.
Callback: mi_<pid>_<i>, mi_skip_<pid>.
"""
import io
import re
import statistics
import time
from datetime import datetime

import ai_kit as K
import garmin_insight as _gi

TAG = "market_insight"
STATE_FILE = "market_insight_state.json"
STORE_FILE = "market_insight_store.json"
MIN_GAP_H = 8
MAX_AGE_H = 5
MIN_POINTS = 100
Z_MIN = 1.6
WEEK_MOVE = 5.0
RECENT_SKIP = 3

_store = K.PayloadStore(STORE_FILE)
_SYM = {"bitcoin": "BTC", "ethereum": "ETH", "binancecoin": "BNB", "ripple": "XRP",
        "solana": "SOL", "tron": "TRX", "hyperliquid": "HYPE", "zcash": "ZEC",
        "dogecoin": "DOGE", "monero": "XMR", "chainlink": "LINK", "cardano": "ADA",
        "stellar": "XLM", "bitcoin-cash": "BCH", "near": "NEAR", "litecoin": "LTC",
        "uniswap": "UNI", "avalanche-2": "AVAX", "ondo-finance": "ONDO"}


def _log(m):
    K.log(TAG, m)


def _history() -> dict:
    try:
        import storage
        return storage.load_price_history() or {}
    except Exception as e:
        _log("history: " + str(e))
        return {}


def _at(pts, ts):
    """значення в найближчій до ts точці (pts відсортовані)"""
    best = min(pts, key=lambda p: abs(p[0] - ts))
    return best[1] if abs(best[0] - ts) <= 5 * 3600 else None


def _holdings() -> dict:
    """{coingecko_id: amount}"""
    try:
        import portfolio
        pos = portfolio.load_positions() or {}
    except Exception:
        return {}
    out = {}
    for sym, p in pos.items():
        try:
            if float(p.get("amount", 0)) > 0:
                out[p.get("coingecko", sym.lower())] = (sym, float(p["amount"]))
        except Exception:
            continue
    return out


def analyse(hist: dict = None, now_ts: float = None) -> list:
    hist = hist if hist is not None else _history()
    now_ts = now_ts or time.time()
    hold = _holdings()
    rows = []
    for cid, pts in hist.items():
        try:
            pts = sorted([(float(t), float(v)) for t, v in pts if v and v > 0])
        except Exception:
            continue
        if len(pts) < MIN_POINTS:
            continue
        last_ts, last = pts[-1]
        if now_ts - last_ts > MAX_AGE_H * 3600:
            continue
        p24 = _at(pts, last_ts - 86400)
        p7 = _at(pts, last_ts - 7 * 86400)
        if not p24:
            continue
        r24 = (last / p24 - 1) * 100
        # власні 24-год рухи монети (для z-score)
        rolls = []
        for ts, v in pts[::3]:
            if ts - pts[0][0] < 86400:
                continue
            b = _at(pts, ts - 86400)
            if b:
                rolls.append((v / b - 1) * 100)
        if len(rolls) < 20:
            continue
        mu, sd = statistics.mean(rolls), statistics.pstdev(rolls) or 0.5
        z = (r24 - mu) / sd
        r7 = (last / p7 - 1) * 100 if p7 else None
        vals = [v for _, v in pts]
        lo, hi = min(vals), max(vals)
        pos = (last - lo) / (hi - lo) if hi > lo else 0.5
        event = ""
        if abs(z) >= Z_MIN:
            event = "рух 24г незвично сильний"
        elif r7 is not None and abs(r7) >= WEEK_MOVE and (pos >= 0.98 or pos <= 0.02):
            event = "новий максимум вікна" if pos >= 0.98 else "новий мінімум вікна"
        if not event:
            continue
        sym, amt = hold.get(cid, (_SYM.get(cid, cid.upper()[:6]), 0.0))
        score = abs(z) * (1.5 if amt else 1.0) * (1.2 if cid in ("bitcoin", "ethereum") else 1.0)
        rows.append({"id": cid, "sym": sym, "last": last, "r24": round(r24, 2),
                     "r7": round(r7, 2) if r7 is not None else None,
                     "z": round(z, 2), "pos": round(pos, 2), "lo": lo, "hi": hi,
                     "amount": amt, "value": round(amt * last, 2) if amt else 0.0,
                     "pnl24": round(amt * (last - p24), 2) if amt else 0.0,
                     "event": event, "score": round(score, 2),
                     "days": round((last_ts - pts[0][0]) / 86400, 1),
                     "last_ts": last_ts})
    rows.sort(key=lambda r: -r["score"])
    return rows


def _state():
    st = K.load(STATE_FILE, default={}) or {}
    return st if isinstance(st, dict) else {}


def pick(rows, state):
    recent = (state.get("recent") or [])[-RECENT_SKIP:]
    for r in rows:
        if r["id"] not in recent:
            return r
    return None


def chart(hist: dict, row: dict) -> bytes:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except Exception as e:
        _log("matplotlib: " + str(e))
        return b""
    pts = sorted((float(t), float(v)) for t, v in hist.get(row["id"], []))
    if len(pts) < 20:
        return b""
    xs = [datetime.utcfromtimestamp(t) for t, _ in pts]
    ys = [v for _, v in pts]
    up = row["r24"] >= 0
    col = "#3FB950" if up else "#F85149"
    fig, ax = plt.subplots(figsize=(8, 4), dpi=110)
    fig.patch.set_facecolor("#0D1117"); ax.set_facecolor("#161B22")
    ax.plot(xs, ys, color=col, lw=1.8)
    ax.fill_between(xs, ys, min(ys), color=col, alpha=0.08)
    ax.plot(xs[-1:], ys[-1:], "o", ms=9, color=col)
    ax.annotate(f"{ys[-1]:,.4g}", (xs[-1], ys[-1]), textcoords="offset points",
                xytext=(-6, 10), ha="right", color=col, fontweight="bold")
    btc = sorted((float(t), float(v)) for t, v in hist.get("bitcoin", []))
    if row["id"] != "bitcoin" and len(btc) > 20:
        k = ys[0] / btc[0][1]
        ax2 = ax.twinx()
        ax2.plot([datetime.utcfromtimestamp(t) for t, _ in btc], [v for _, v in btc],
                 color="#58A6FF", lw=1, alpha=0.6, ls="--", label="BTC")
        ax2.tick_params(colors="#58A6FF", labelsize=7)
        ax2.legend(facecolor="#161B22", edgecolor="#30363D", labelcolor="#58A6FF",
                   loc="upper left", fontsize=8)
    ax.set_title(f"{row['sym']} · ${row['last']:,.4g} · {row['days']:.0f} дн.",
                 color="#E6EDF3", fontsize=13, loc="left")
    ax.tick_params(colors="#8B949E")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    for sp in ax.spines.values():
        sp.set_color("#30363D")
    ax.grid(color="#30363D", lw=0.5, alpha=0.6)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


_PROMPT = """Ти особистий крипто-аналітик Олега (Кошице; ціль — фінансова
незалежність; довгостроковий інвестор, не трейдер). Нижче РЕАЛЬНІ дані
DefiLlama. Нічого не вигадуй.

МОНЕТА: {sym}. Ціна ${last:,.6g}. Подія: {event}.
Зміна 24 год: {r24:+.2f}% (z={z:+.1f} відносно власних рухів монети).
Зміна 7 днів: {r7}. Положення у діапазоні {days} дн.: {pos:.0%} (мін ${lo:,.6g}, макс ${hi:,.6g}).
{holding}
BTC за 24 год: {btc24}.

Напиши українською, без привітання і без кліше, одразу до суті:
"text": 3-5 коротких речень HTML (<b> для 1-2 чисел). Це монетний рух чи
ринковий (порівняй з BTC)? Що це означає для довгострокової цілі, а не для
угадування завтрашньої ціни. Одна конкретна дія/перевірка. Без порад «купуй/продавай
зараз», лише що варто перевірити чи обдумати.
"question": одне коротке питання до Олега по цій позиції/сигналу.
"options": 2-4 варіанти відповіді, що ПРЯМО відповідають питанню (до 28 симв.), це лише ЗАПИС відповіді: не обіцяй, що бот щось купить/нагадає/поставить.
Формат — ТІЛЬКИ JSON-об'єкт: {{"text":"...","question":"...","options":["..",".."]}}"""


def build(row, btc24):
    if row["amount"]:
        holding = (f"У портфелі Олега: {row['amount']:g} {row['sym']} ≈ ${row['value']:,.2f}; "
                   f"зміна позиції за 24 год ≈ {row['pnl24']:+,.2f} USD.")
    else:
        holding = "У портфелі Олега цієї монети немає (це спостереження за ринком)."
    prompt = _PROMPT.format(
        sym=row["sym"], last=row["last"], event=row["event"], r24=row["r24"], z=row["z"],
        r7=(f"{row['r7']:+.2f}%" if row["r7"] is not None else "немає даних"),
        days=row["days"], pos=row["pos"], lo=row["lo"], hi=row["hi"],
        holding=holding, btc24=btc24)
    data = K.gemini_json(prompt, max_tokens=800, temperature=0.85, tag="market_insight", want="dict")
    if not isinstance(data, dict) or not data.get("text"):
        return {}
    opts = [str(o).strip()[:28] for o in (data.get("options") or []) if str(o).strip()][:4]
    return {"text": str(data["text"]).strip(), "question": str(data.get("question") or "").strip(),
            "options": opts}


def run(force=False) -> bool:
    try:
        import quiet
        if not force and quiet.blocked("msg"):
            return False
    except Exception:
        pass
    if not force:
        try:
            import dismissed
            if dismissed.is_muted("crypto") or dismissed.is_muted("market_insight"):
                return False
        except Exception:
            pass
        if not K.rate_ok("market_insight_gap.json", MIN_GAP_H * 60):
            return False
        # 13.11: БЕЗ topic_ok — старі крипто-шаблони постійно ставили мітку й
        # блокували цей розбір. Власний інтервал 8 год; мітку ставимо після відправки.
    hist = _history()
    rows = analyse(hist)
    state = _state()
    row = pick(rows, state)
    if not row:
        _log("подій нема (або монету вже розбирали) — мовчу")
        return False
    K.rate_mark("market_insight_gap.json")
    btc = _btc24(hist)
    built = build(row, btc)
    if not built:
        return False
    png = chart(hist, row)
    arrow = "🟢" if row["r24"] >= 0 else "🔴"
    head = (f"{arrow} <b>{K.esc(row['sym'])}: ${row['last']:,.4g}</b> · 24г {row['r24']:+.1f}%"
            + (f" · 7д {row['r7']:+.1f}%" if row["r7"] is not None else ""))
    pid = _store.put({"id": row["id"], "sym": row["sym"], "q": built["question"],
                      "opts": built["options"], "r24": row["r24"]})
    kb = None
    q = ("\n\n❓ <b>" + K.esc(built["question"]) + "</b>") if built["question"] else ""
    if built["question"] and built["options"]:
        kb = [[{"text": o, "callback_data": f"mi_{pid}_{i}"}] for i, o in enumerate(built["options"])]
        kb.append([{"text": "🙈 Не пиши про цю монету", "callback_data": f"mi_skip_{pid}"}])
    if png:
        ok = _gi._send_photo(png, head)
        if ok:
            ok = K.send_card(built["text"] + q, kb, tag=TAG)
    else:
        ok = K.send_card(head + "\n\n" + built["text"] + q, kb, tag=TAG)
    if not ok:
        _store.drop(pid)
        return False
    K.topic_mark("crypto")
    state["recent"] = ((state.get("recent") or []) + [row["id"]])[-6:]
    K.save(STATE_FILE, state)
    _log(f"✅ {row['sym']} z={row['z']}")
    return True


def _btc24(hist):
    pts = sorted((float(t), float(v)) for t, v in hist.get("bitcoin", []))
    if len(pts) < 10:
        return "немає даних"
    b = _at(pts, pts[-1][0] - 86400)
    return f"{(pts[-1][1] / b - 1) * 100:+.2f}%" if b else "немає даних"


def handle(data: str) -> dict:
    m = re.match(r"^mi_skip_(\w+)$", data)
    if m:
        p = _store.get(m.group(1)) or {}
        _store.drop(m.group(1))
        if p.get("id"):
            st = _state()
            st["recent"] = ((st.get("recent") or []) + [p["id"]] * RECENT_SKIP)[-9:]
            K.save(STATE_FILE, st)
        return {"ok": True, "text": "Ок, цю монету не чіпаю кілька розборів."}
    m = re.match(r"^mi_(\w+)_(\d)$", data)
    if not m:
        return {"ok": False, "text": "Невідома дія"}
    p = _store.get(m.group(1))
    if not p:
        return {"ok": False, "text": "Це питання вже закрите."}
    opts = p.get("opts") or []
    i = int(m.group(2))
    if i >= len(opts):
        return {"ok": False, "text": "Немає такого варіанта."}
    try:
        import ai_brain
        ai_brain.remember_answer(f"{p.get('sym')} {p.get('r24'):+.1f}% за 24г: {p.get('q')}",
                                 opts[i], topic="crypto", source="button")
    except Exception as e:
        _log("remember: " + str(e))
    _store.drop(m.group(1))
    return {"ok": True, "text": f"Записав: {opts[i]}"}


if __name__ == "__main__":
    import sys
    if "--dry" in sys.argv:
        for r in analyse():
            print(r["sym"], r["r24"], r["r7"], r["z"], r["event"], r["score"])
    else:
        print(run(force=True))
