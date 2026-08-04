#!/usr/bin/env python3
"""
ЛИСТ ПОСТАЧАЛЬНИКУ ПО РАХУНКУ  (Гроші #2)

Продовження bills_watcher: під карточкою рахунку є кнопка
«✍️ Написати постачальнику». Вона відкриває 4 сценарії:

  ❓ Уточнити рахунок   — за що саме, звідки сума, попросити деталізацію
  ⏳ Попросити відстрочку — коректне прохання перенести оплату
  ✅ Повідомити про оплату — «оплатив, ось дата, прошу підтвердити»
  ⚠️ Оскаржити          — сума/послуга не відповідає, прошу перевірити

AI пише лист у ділових тонах МОВОЮ ЛИСТА постачальника (словацька, англійська
або українська — визначає сам), 90-140 слів, без вигаданих фактів: оперує лише
тим, що є в рахунку (постачальник, сума, номер, дедлайн).

Нічого не надсилається одразу: спершу черновик у Telegram, надсилання —
лише окремою кнопкою «📤 Надіслати».

Callback-префікси: vr_menu_ / vr_info_ / vr_delay_ / vr_paid_ / vr_dispute_ /
                   vr_send_ / vr_skip_
"""

import re

import ai_kit as K

TAG = "vendor_reply"

STORE_FILE = "vendor_reply_store.json"
LOG_FILE = "vendor_reply_log.json"

_store = K.PayloadStore(STORE_FILE)

MY_NAME = "Oleh Novosadov"
MY_EMAIL = "novosadovoleg@gmail.com"

SCENARIOS = {
    "info": {
        "icon": "❓", "label": "Уточнити рахунок",
        "goal": ("Ввічливо попросити деталізацію: за який період і які саме "
                 "позиції входять у суму, на якій підставі нарахування."),
    },
    "delay": {
        "icon": "⏳", "label": "Попросити відстрочку",
        "goal": ("Коректно попросити перенести оплату на 10-14 днів, "
                 "підтвердити намір оплатити повністю, запитати чи можливо "
                 "без пені."),
    },
    "paid": {
        "icon": "✅", "label": "Повідомити про оплату",
        "goal": ("Повідомити, що рахунок оплачено, вказати номер рахунку "
                 "і попросити підтвердити зарахування та закрити заборгованість."),
    },
    "dispute": {
        "icon": "⚠️", "label": "Оскаржити суму",
        "goal": ("Ввічливо, але твердо повідомити, що сума або послуга не "
                 "відповідає очікуваному, попросити перевірку і виправлений "
                 "рахунок, оплату поставити на паузу до з'ясування."),
    },
}

_PROMPT = """Ти пишеш ділового email від імені Олега Новосадова (Кошице, Словаччина)
до постачальника по рахунку.

ДАНІ РАХУНКУ (лише це — реально відомо):
Постачальник: {vendor}
Сума: {amount}
Номер рахунку: {invoice}
Дедлайн оплати: {due}
Email постачальника: {to}
Тема оригінального листа: {subject}
Примітка: {note}

МЕТА ЛИСТА: {goal}

ПРАВИЛА:
1. НЕ вигадуй фактів: жодних дат оплати, номерів платежів, сум, яких немає вище.
   Якщо для мети потрібна деталь, якої немає — попроси її або напиши нейтрально.
2. МОВА ЛИСТА: та сама, якою написаний оригінал (визнач по темі й назві
   постачальника). Словацький постачальник — словацька; міжнародний — англійська;
   український — українська.
3. Обсяг: 90-140 слів. Тон: ділово, ввічливо, без емоцій і без вибачень зайвих.
4. Структура: звертання → суть (1-2 речення) → конкретне прохання → підпис.
5. Підпис: {me} ({my_email}).
6. Поверни ТІЛЬКИ текст листа. Без markdown, без пояснень, без темы листа.
"""


def _fmt_amount(p) -> str:
    a = str(p.get("amount") or "").strip()
    if not a:
        return "не вказана в листі"
    return f"{a} {p.get('currency') or 'EUR'}"


def _sender_email(p) -> str:
    """Витягує email постачальника: з payload, з bills.json або з кешу листів."""
    raw = str(p.get("sender") or "")
    m = re.search(r"[\w.\-+]+@[\w.\-]+\.\w+", raw)
    if m:
        return m.group(0)
    uid = str(p.get("uid") or "")
    if uid:
        bodies = K.load("email_body_cache.json", default={}) or {}
        rec = bodies.get(uid) or {}
        m = re.search(r"[\w.\-+]+@[\w.\-]+\.\w+", str(rec.get("sender") or ""))
        if m:
            return m.group(0)
    try:
        import bills_watcher as B
        b = (B.load_bills() or {}).get(p.get("bid") or "") or {}
        m = re.search(r"[\w.\-+]+@[\w.\-]+\.\w+", str(b.get("sender") or ""))
        if m:
            return m.group(0)
    except Exception:
        pass
    return ""


# ─── МЕНЮ СЦЕНАРІЇВ ──────────────────────────────────────────────────────────

def menu(pid: str) -> dict:
    """Кнопка «✍️ Написати постачальнику» — показуємо 4 сценарії."""
    p = _store_from_bills(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    to = _sender_email(p)
    if not to:
        return {"ok": False, "error": "no_vendor_email"}
    pid2 = _store.put({**p, "to": to})
    kb = [
        [{"text": "❓ Уточнити рахунок", "callback_data": f"vr_info_{pid2}"}],
        [{"text": "⏳ Попросити відстрочку", "callback_data": f"vr_delay_{pid2}"}],
        [{"text": "✅ Повідомити про оплату", "callback_data": f"vr_paid_{pid2}"}],
        [{"text": "⚠️ Оскаржити суму", "callback_data": f"vr_dispute_{pid2}"}],
        [{"text": "❌ Не треба", "callback_data": f"vr_skip_{pid2}"}],
    ]
    text = (f"✍️ <b>ЛИСТ ПОСТАЧАЛЬНИКУ</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🏢 {K.esc(p.get('vendor'))}\n"
            f"💰 {K.esc(_fmt_amount(p))}\n"
            f"📧 {K.esc(to)}\n\n"
            f"Що написати? Я складу черновик — надсилати будеш ти окремою кнопкою.")
    return {"ok": True, "text": text, "keyboard": kb, "pid": pid2, "to": to}


def _store_from_bills(pid: str):
    """Payload міг бути створений bills_watcher (bills_store) або нами."""
    p = _store.get(pid)
    if p:
        return p
    try:
        import bills_watcher as B
        return B._store.get(pid)
    except Exception:
        return None


# ─── ЧЕРНОВИК ────────────────────────────────────────────────────────────────

def draft(pid: str, kind: str) -> dict:
    p = _store_from_bills(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    sc = SCENARIOS.get(kind)
    if not sc:
        return {"ok": False, "error": "bad_kind"}
    to = p.get("to") or _sender_email(p)
    if not to:
        return {"ok": False, "error": "no_vendor_email"}

    body = K.gemini_text(_PROMPT.format(
        vendor=p.get("vendor") or "постачальник",
        amount=_fmt_amount(p),
        invoice=p.get("invoice_no") or "не вказаний",
        due=p.get("due") or "не вказаний",
        to=to,
        subject=p.get("subject") or p.get("note") or "рахунок",
        note=p.get("note") or "—",
        goal=sc["goal"], me=MY_NAME, my_email=MY_EMAIL),
        max_tokens=800, temperature=0.5, tag=TAG)
    if not body:
        return {"ok": False, "error": "ai_unavailable"}
    body = re.sub(r"^```.*?\n|```$", "", body.strip(), flags=re.DOTALL).strip()
    body = re.sub(r"^(?:Subject|Тема|Predmet)\s*:.*\n+", "", body, flags=re.I)

    subject = _subject(p, kind)
    pid2 = _store.put({**p, "to": to, "draft": body[:2200],
                       "subject_out": subject, "kind": kind})
    return {"ok": True, "pid": pid2, "to": to, "subject": subject,
            "draft": body[:2200], "label": sc["label"], "icon": sc["icon"],
            "vendor": p.get("vendor")}


def _subject(p, kind) -> str:
    inv = str(p.get("invoice_no") or "").strip()
    base = p.get("subject") or ""
    if base:
        s = base if base.lower().startswith("re:") else f"Re: {base}"
        return s[:120]
    tail = f" č. {inv}" if inv else ""
    names = {"info": "Žiadosť o detail faktúry", "delay": "Žiadosť o odklad platby",
             "paid": "Potvrdenie platby", "dispute": "Nesúlad vo faktúre"}
    return (names.get(kind, "Faktúra") + tail)[:120]


# ─── НАДСИЛАННЯ ──────────────────────────────────────────────────────────────

def send(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    if not p.get("draft"):
        return {"ok": False, "error": "no_draft"}
    try:
        import assistant
        res = assistant.send_email_reply(p["to"], p.get("subject_out") or "Faktúra",
                                         p["draft"])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "send_error")}

    _store.drop(pid)
    K.update_key(LOG_FILE, K.now().strftime("%Y%m%d%H%M%S"), {
        "vendor": p.get("vendor"), "to": p["to"], "kind": p.get("kind"),
        "amount": _fmt_amount(p), "subject": p.get("subject_out"),
        "ts": K.now().isoformat(),
    })
    try:
        import response_log
        response_log.log_response("vendor_reply_sent", p.get("vendor"),
                                  p.get("kind"), {"to": p["to"]})
    except Exception:
        pass
    return {"ok": True, "to": p["to"], "vendor": p.get("vendor"),
            "kind": p.get("kind")}


def do_skip(pid: str) -> dict:
    _store.drop(pid)
    return {"ok": True}


def bills_cards(limit: int = 6) -> list:
    """Живий список рахунків з bills.json зі СВІЖИМ payload під кожним.

    Потрібно, бо payload карточки рахунку (bills_store.json) підчищається
    після натискання кнопок — і стара кнопка «✍️ Написати постачальнику»
    ставала мертвою. Тут payload створюється заново, тому кнопка завжди робоча.
    """
    try:
        import bills_watcher as B
        bills = B.load_bills() or {}
    except Exception as e:
        print(f"[{TAG}] bills load error: {e}", flush=True)
        return []
    if not bills:
        return []

    def _key(item):
        b = item[1] or {}
        return (bool(b.get("paid")), str(b.get("created") or ""))

    rows = sorted(bills.items(), key=_key, reverse=True)
    # неоплачені — першими, потім найсвіжіші оплачені
    rows = sorted(rows, key=lambda it: bool((it[1] or {}).get("paid")))
    cards = []
    for bid, b in rows[:limit]:
        if not isinstance(b, dict):
            continue
        pay = {"bid": bid, "vendor": b.get("vendor"), "amount": b.get("amount"),
               "currency": b.get("currency"), "invoice_no": b.get("invoice_no"),
               "due": b.get("due"), "uid": b.get("uid"), "note": b.get("note"),
               "sender": b.get("sender"), "subject": b.get("subject")}
        to = _sender_email(pay)
        if not to:
            continue
        pay["to"] = to
        pid = _store.put(pay)
        status = "✅ оплачено" if b.get("paid") else "⏳ не оплачено"
        due = f"\n📅 до {K.esc(b.get('due'))}" if b.get("due") else ""
        inv = f"\n🧾 {K.esc(b.get('invoice_no'))}" if b.get("invoice_no") else ""
        text = (f"🧾 <b>{K.esc(b.get('vendor') or 'постачальник')}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 {K.esc(_fmt_amount(pay))}{inv}{due}\n"
                f"📧 {K.esc(to)}\n"
                f"📌 {status}")
        cards.append({"text": text, "pid": pid, "vendor": b.get("vendor"),
                      "keyboard": [[{"text": "✍️ Написати постачальнику",
                                     "callback_data": f"vr_menu_{pid}"}]]})
    return cards


def history(limit: int = 15) -> str:
    log = K.load(LOG_FILE, default={}) or {}
    if not log:
        return ("✍️ <b>ЛИСТИ ПОСТАЧАЛЬНИКАМ</b>\n\nЩе жодного не надсилали.\n\n"
                "<i>Кнопка «✍️ Написати постачальнику» є під кожним рахунком.</i>")
    rows = sorted(log.items(), reverse=True)[:limit]
    out = ["✍️ <b>ЛИСТИ ПОСТАЧАЛЬНИКАМ</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for _, r in rows:
        sc = SCENARIOS.get(r.get("kind") or "", {})
        out.append(f"{sc.get('icon', '📧')} <b>{K.esc(r.get('vendor'))}</b> — "
                   f"{K.esc(sc.get('label') or r.get('kind'))}")
        out.append(f"    {K.esc(r.get('to'))} · {str(r.get('ts'))[:16].replace('T', ' ')}")
    return "\n".join(out)[:3900]


if __name__ == "__main__":
    import sys
    if "--history" in sys.argv:
        print(history())
    elif "--bills" in sys.argv:
        for c in bills_cards():
            print(c["pid"], "|", c["vendor"])
    else:
        print("модуль викликається з кнопок під рахунком (bills_watcher)")
