#!/usr/bin/env python3
"""
astro_delta.py — що в астро-транзитах НОВЕ, а що вже розбирали.

Проблема (скарга Олега): астро-розбір двічі на день щоразу перелічує ті самі
повільні транзити (Сатурн □ Юпітер 0.1° тримається тижнями) на 800-1000 слів.
Тут за реальним списком транзитів і журналом «що вже розбирали» формується блок:

  НОВЕ/ЗМІНИЛОСЬ   — аспект, якого не було в останніх розборах, або орб став
                     точним (≤0.5°) вперше, або Місяць (швидкий — щодня інший);
  ВЖЕ РОЗБИРАЛИ    — повільний аспект, який згадували ≥2 рази за 7 днів
                     (AI: не розписувати, максимум одне слово).

Стан — astro_delta_state.json. mark() викликається ТІЛЬКИ після успішної
відправки розбору.
"""
import re
from datetime import datetime, timedelta

import ai_kit as K

FILE = "astro_delta_state.json"
LINE_RE = re.compile(
    r"^\S+\s+(?P<tp>\S+(?: \S+)?)\s+(?P<asp>Квадрат|Опозиція|Трин|Секстиль|Кон'юнкція|Квінконкс)"
    r"\s+\S+\s+натальний\s+(?P<np>.+?)\s+<i>\((?P<orb>[\d.]+)°\)", re.M)
FAST = ("Місяць",)
REPEAT_DAYS = 7
REPEAT_MAX = 2
EXACT = 0.5


def _clean(s: str) -> str:
    return re.sub(r"<(?!/?i>)[^>]+>", "", s or "")


def parse(transit_html: str) -> list:
    out = []
    for m in LINE_RE.finditer(_clean(transit_html)):
        tp = re.sub(r"^[^\w]+", "", m.group("tp")).strip()
        np_ = re.sub(r"^[^\w]+", "", m.group("np")).strip()
        out.append({"key": f"{tp}|{m.group('asp')}|{np_}", "tp": tp, "asp": m.group("asp"),
                    "np": np_, "orb": float(m.group("orb"))})
    return out


def _state() -> dict:
    st = K.load(FILE, default={}) or {}
    return st if isinstance(st, dict) else {}


def classify(transit_html: str, state: dict = None, today: str = None):
    state = state if state is not None else _state()
    today = today or K.today_str()
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=REPEAT_DAYS)).strftime("%Y-%m-%d")
    new, old = [], []
    for a in parse(transit_html):
        hist = [d for d in (state.get(a["key"], {}).get("m") or []) if d >= cutoff]
        if a["tp"] in FAST or not hist:
            new.append(a)
        elif a["orb"] <= EXACT and not state.get(a["key"], {}).get("exact"):
            a["why"] = "орб став точним"
            new.append(a)
        elif len(hist) >= REPEAT_MAX:
            old.append(a)
        else:
            new.append(a)
    return new, old


def block(transit_html: str) -> str:
    try:
        new, old = classify(transit_html)
    except Exception:
        return ""
    if not new and not old:
        return ""
    fmt = lambda a: f"{a['tp']} {a['asp']} нат.{a['np']} ({a['orb']}°{', ' + a['why'] if a.get('why') else ''})"
    lines = []
    if new:
        lines.append("НОВЕ/ЗМІНИЛОСЬ (про це пиши): " + "; ".join(fmt(a) for a in new[:8]))
    if old:
        lines.append("ВЖЕ РОЗБИРАЛИ ≥2 рази за тиждень (НЕ розписуй, максимум згадай одним реченням "
                     "якщо це справді важливо): " + "; ".join(fmt(a) for a in old[:8]))
    return "\n".join(lines)


def mark(transit_html: str) -> None:
    """Записує, що ці аспекти щойно були в розборі."""
    today = K.today_str()
    st = _state()
    for a in parse(transit_html):
        rec = st.get(a["key"], {})
        m = [d for d in (rec.get("m") or []) if d != today][-6:] + [today]
        rec["m"] = m
        if a["orb"] <= EXACT:
            rec["exact"] = today
        st[a["key"]] = rec
    # прибираємо старе
    cut = (K.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    st = {k: v for k, v in st.items() if (v.get("m") or [""])[-1] >= cut}
    K.save(FILE, st)
