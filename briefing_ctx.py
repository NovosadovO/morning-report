#!/usr/bin/env python3
"""
briefing_ctx.py — вступ-брифінг у ранковому/денному звіті на РЕАЛЬНИХ сигналах.

Скарга Олега (13.11): «текст старий і однаковий». Старий промпт вимагав «мінімум
500 слів», «ВЕКТОР/ІНСАЙТ/3 ДІЇ» щоразу, і AI вигадував загальну філософію
(«баланс між Юпітером і Сатурном», «міні-прогулянка»).
Тепер: короткий текст (≤~200 слів), кожна дія з конкретним фактом, астро лише
якщо є НОВИЙ транзит, AI бачить 3 попередні вступи і не повторюється.
"""
import re

import ai_kit as K

STATE_FILE = "briefing_prev.json"
MAX_PREV = 3


def _log(m):
    print(f"[briefing_ctx] {m}", flush=True)


def signals(astro_raw: str = "") -> str:
    """Реальні сигнали з окремих модулів; що не вдалось — просто пропускаємо."""
    out = []
    try:
        import run_insight as R
        rs = R.runs()
        rd = R.readiness(rs=rs)
        pl = R.plan(rd, rs)
        if rd.get("level") in ("green", "yellow", "red"):
            out.append(f"ГОТОВНІСТЬ ДО БІГУ: {rd['level']} ({'; '.join(rd.get('reasons') or []) or 'без застережень'}). "
                       f"План: {pl.get('title', '')}")
        else:
            out.append("ГОТОВНІСТЬ ДО БІГУ: невідома (немає свіжих замірів) — не вгадуй")
        if rs:
            r = rs[-1]
            d = r["date"].strftime("%d.%m") if hasattr(r.get("date"), "strftime") else str(r.get("date"))
            out.append(f"Остання пробіжка: {d}, {r.get('km')} км, темп {R.fmt_pace(r.get('pace'))}"
                       + (f", пульс {int(r['hr'])}" if r.get("hr") else ""))
    except Exception as e:
        _log("run: " + str(e))
    try:
        import garmin_fresh
        b = garmin_fresh.block()
        if b:
            out.append("GARMIN (з віком даних):\n" + b)
    except Exception as e:
        _log("garmin: " + str(e))
    try:
        import calendar_watch as cw
        c = cw.ai_context_text(past_hours=3, ahead_days=2, limit=10) or ""
        if c:
            out.append("КАЛЕНДАР: " + c[:600])
    except Exception as e:
        _log("cal: " + str(e))
    try:
        import astro_delta
        a = astro_delta.block(astro_raw) if astro_raw else ""
        out.append("АСТРО-ТРАНЗИТИ: " + a if a else
                   "АСТРО-ТРАНЗИТИ: нічого нового проти минулих розборів — астро в брифінг НЕ включай")
    except Exception as e:
        _log("astro: " + str(e))
    return "\n".join(out)


def prev_topics() -> list:
    try:
        import storage
        d = storage.load(STATE_FILE, default={}) or {}
        return list(d.get("prev") or [])[-MAX_PREV:]
    except Exception:
        return []


def remember(text: str) -> None:
    t = re.sub(r"<[^>]+>", "", text or "").strip().replace("\n", " ")[:260]
    if not t:
        return
    try:
        import storage
        d = storage.load(STATE_FILE, default={}) or {}
        d["prev"] = (list(d.get("prev") or [])[-(MAX_PREV - 1):] + [t])
        d["at"] = K.today_str()
        storage.save(STATE_FILE, d)
    except Exception as e:
        _log("remember: " + str(e))


def build_prompt(now_local, shift_hint: str, report_ctx: str, user_state: str,
                 sig: str, prev: list, seed: str) -> str:
    prev_txt = "\n".join(f"- {p}" for p in prev) if prev else "(нема)"
    return (
        f"Ти — персональний асистент Олега Новосадова (Кошіце, Словаччина). "
        f"Зараз {now_local.strftime('%H:%M')}, {now_local.strftime('%d.%m.%Y')}.\n"
        f"СТАТУС ЗМІНИ: {shift_hint}\n"
        f"Якщо 'НА РОБОТІ'/'нічна зміна йде' — Олег на заводі, не пиши що вдома. "
        f"Якщо невідомо — без слів 'вдома'/'відпочиваєш'.\n\n"
        f"=== ЗВІТ (вже побачить нижче, цифри НЕ повторюй) ===\n{report_ctx}\n=================\n\n"
        f"=== РЕАЛЬНІ СИГНАЛИ ===\n{sig}\n=======================\n\n"
        f"ПОТОЧНИЙ СТАН: {user_state or '—'}\n\n"
        f"ТВОЇ ПОПЕРЕДНІ ВСТУПИ (НЕ повторюй ні тему, ні формулювання):\n{prev_txt}\n\n"
        f"ЗАВДАННЯ: коротка записка (до 150–200 слів) про те, що саме СЬОГОДНІ змінилось або вимагає уваги. "
        f"Пріоритет тем: 1) здоров'я і біг (Garmin, готовність) 2) крипто/гроші 3) календар і пошта 4) астро.\n"
        f"Формат:\n"
        f"🧭 ГОЛОВНЕ — 2–3 речення, одна головна річ дня з конкретним фактом (число, час, назва події).\n"
        f"🎯 ДІЇ — від 0 до 3 пунктів. Кожен: що зробити + конкретний факт з даних, чому саме зараз. "
        f"Немає чого — напиши менше пунктів або жодного; не вигадуй.\n"
        f"Астро — лише ОДНЕ речення і лише якщо у сигналах є НОВИЙ транзит; без загальної філософії.\n\n"
        f"ЗАБОРОНЕНО: привітання; «баланс між Юпітером і Сатурном» та інша загальна астро-вода; "
        f"порожні поради (випий води, прогуляйся, плануй пробіжку) без прив'язки до даних; "
        f"markdown (**, ###, ---); вигадані числа — бери лише з даних вище. "
        f"Дані мають вік — якщо Garmin старий, так і скажи. Кожне речення завершуй. [seed:{seed}]"
    )
