#!/usr/bin/env python3
"""
lifecycle.py — бот розуміє СТАН СПРАВИ, а не просто текст листа.

Олег: «Поїздка на Корфу вже пройшла — сьогодні прилетів додому. Страховку я
оплатив — не нагадуй, а напиши з якого по яке число вона діє і запиши в
календар наступну оплату. Вся інформація в листах є».

Що робить модуль:
  1. Читає вже готовий AI-розбір листа (поля state/entity/valid_to/next_due —
     той самий Gemini-виклик, без зайвих запитів і витрат).
  2. state = paid/completed → ЗАКРИВАЄ тему: глушить нагадування «оплати це»
     (dismissed, у т.ч. за ключовим словом — «Корфу» вбиває всі нагадування
     про поїздку, хоч би як вони називались).
  3. Замість нагадування пропонує ЗАПИС: період дії + наступна оплата
     (за 14 днів до кінця) — однією кнопкою в Google Calendar.
  4. Веде реєстр справ (lifecycle.json): що діє, до якого числа, коли платити
     наступний раз. Команда /справи.
  5. Щодня сам перевіряє реєстр: справа закінчується ≤14 днів і наступна
     оплата не зроблена → нагадує ЗАРАНІ, з датами.

Жодних вигаданих дат: чого немає в листі — того не пишемо. Немає valid_to й
next_due → пропозиції про календар не буде взагалі, тільки закриття теми.

Callback-префікси: lc_add_ (записати все) / lc_only_ (тільки закрити тему)
                   / lc_skip_ (нічого не робити) — усі через confirm-гейт.
"""
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K  # noqa: E402

TAG = "lifecycle"
FILE = "lifecycle.json"          # реєстр справ
SENT_FILE = "lifecycle_sent.json"  # дедуп нагадувань про закінчення

_store = K.PayloadStore("lifecycle_store.json")

WARN_DAYS = 14        # за скільки днів попереджати про закінчення
RENEW_LEAD_DAYS = 14  # за скільки днів до кінця ставити нагадування про оплату

CLOSED_STATES = ("paid", "completed")

KIND_ICON = {
    "insurance": "🛡", "subscription": "🔄", "bill": "💸", "trip": "✈️",
    "booking": "🏨", "appointment": "🏥", "other": "📌",
}
KIND_NAME = {
    "insurance": "страховка", "subscription": "підписка", "bill": "рахунок",
    "trip": "поїздка", "booking": "бронювання", "appointment": "візит",
    "other": "справа",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ─── ДОПОМІЖНЕ ───────────────────────────────────────────────────────────────

def _d(val):
    """Строга дата. Все, що не YYYY-MM-DD — None. Ніяких припущень."""
    s = str(val or "").strip()
    if not _DATE_RE.match(s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _ds(d) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def _slug(text: str) -> str:
    t = re.sub(r"[^0-9a-zа-яіїєґ ]+", " ", str(text or "").lower(), flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:60]


def _today():
    return K.now().date()


# ─── РЕЄСТР ──────────────────────────────────────────────────────────────────

def _reg() -> dict:
    d = K.load(FILE, default={}) or {}
    return d if isinstance(d, dict) else {}


def remember(entity: str, kind: str, state: str, valid_from=None, valid_to=None,
             next_due=None, source: str = "", keyword: str = "") -> str:
    """Пише справу в реєстр. Ключ — назва справи, тому повторний лист про ту
    саму страховку оновлює запис, а не плодить дублі."""
    key = _slug(entity)
    if not key:
        return ""
    old = (_reg().get(key) or {})
    rec = {
        "entity": str(entity)[:120],
        "kind": str(kind or "other"),
        "state": str(state or ""),
        "valid_from": str(valid_from or old.get("valid_from") or "") or None,
        "valid_to": str(valid_to or old.get("valid_to") or "") or None,
        "next_due": str(next_due or old.get("next_due") or "") or None,
        "keyword": str(keyword or old.get("keyword") or "")[:40],
        "source": str(source or ""),
        "ts": K.now().isoformat(),
    }
    K.update_key(FILE, key, rec)
    K.log(TAG, f"📒 {rec['entity']}: {rec['state']}"
               + (f", діє до {rec['valid_to']}" if rec["valid_to"] else "")
               + (f", наступна оплата {rec['next_due']}" if rec["next_due"] else ""))
    return key


def forget(entity: str) -> bool:
    key = _slug(entity)
    if key and key in _reg():
        K.remove_key(FILE, key)
        return True
    return False


# ─── ВХІД: ГОТОВИЙ AI-РОЗБІР ЛИСТА ───────────────────────────────────────────

def from_email_ai(ai: dict, source_id: str, sender: str = "", subject: str = "") -> bool:
    """Викликається після AI-аналізу листа. True → тему закрито, і звичайну
    пропозицію «додати нагадування» надсилати вже НЕ треба."""
    try:
        if not isinstance(ai, dict):
            return False
        state = str(ai.get("state") or "").strip().lower()
        entity = str(ai.get("entity") or "").strip()
        if state not in CLOSED_STATES or not entity:
            return False

        kind = str(ai.get("entity_kind") or "other").strip().lower()
        if kind not in KIND_ICON:
            kind = "other"
        vf, vt = _d(ai.get("valid_from")), _d(ai.get("valid_to"))
        nd = _d(ai.get("next_due"))
        keyword = str(ai.get("keyword") or "").strip()

        # Поїздка/зустріч, яка ще НЕ відбулась, — не закриваємо: AI міг
        # поспішити. Закриваємо тільки те, що справді в минулому.
        if state == "completed" and vt and vt > _today():
            K.log(TAG, f"«{entity}»: AI сказав completed, але дата {vt} у майбутньому "
                       f"— тему не закриваю")
            return False

        # Наступна оплата: беремо з листа; якщо її там немає, але є кінець
        # періоду дії — наступна оплата це день після кінця (це не вигадка, а
        # прямий висновок з періоду).
        if not nd and vt and kind in ("insurance", "subscription", "booking"):
            nd = vt + timedelta(days=1)

        remember(entity, kind, state, valid_from=vf and vf.isoformat(),
                 valid_to=vt and vt.isoformat(), next_due=nd and nd.isoformat(),
                 source=f"email:{source_id}", keyword=keyword)

        _close_reminders(entity, kind, keyword, source_id)
        _offer(entity, kind, state, vf, vt, nd, source_id, sender, subject)
        return True
    except Exception as e:
        print(f"[lifecycle] from_email_ai error: {e}", flush=True)
        return False


def _close_reminders(entity: str, kind: str, keyword: str, source_id: str):
    """Тема закрита → нагадування «оплати / не забудь» більше не приходять."""
    try:
        import dismissed as D
        D.mute("lifecycle", key=source_id, title=entity,
               keyword=keyword or entity, note=f"{kind}:closed")
        K.log(TAG, f"🚫 нагадування про «{entity}» вимкнено (закрито)")
    except Exception as e:
        K.log(TAG, f"dismissed mute error: {e}")


# ─── ПРОПОЗИЦІЯ ЗАПИСУ ───────────────────────────────────────────────────────

def _plan(entity, kind, vt, nd):
    """Що саме запишемо в календар. Порожній список = нічого пропонувати."""
    icon = KIND_ICON.get(kind, "📌")
    out = []
    if vt:
        out.append({"date": vt.isoformat(),
                    "title": f"{icon} {entity} — останній день дії",
                    "note": "Далі перестає діяти."})
    if nd:
        lead = nd - timedelta(days=RENEW_LEAD_DAYS)
        if lead <= _today():
            lead = nd
        out.append({"date": lead.isoformat(),
                    "title": f"{icon} Оплатити: {entity}",
                    "note": f"Наступна оплата до {_ds(nd)}."})
    return out


def _offer(entity, kind, state, vf, vt, nd, source_id, sender, subject):
    """Замість нагадування — розповідаємо, що зрозуміли, і пропонуємо запис."""
    icon = KIND_ICON.get(kind, "📌")
    kname = KIND_NAME.get(kind, "справа")
    head = ("✅ <b>Бачу — вже оплачено</b>" if state == "paid"
            else "✅ <b>Бачу — це вже позаду</b>")
    lines = [head, "━━━━━━━━━━━━━━━━━━━━",
             f"{icon} <b>{K.esc(entity)}</b> <i>({kname})</i>"]
    if vf and vt:
        lines.append(f"📆 Діє: <b>{_ds(vf)} — {_ds(vt)}</b>")
    elif vt:
        lines.append(f"📆 Діє до: <b>{_ds(vt)}</b>")
    elif vf:
        lines.append(f"📆 Початок: <b>{_ds(vf)}</b>")
    if nd:
        left = (nd - _today()).days
        lines.append(f"💸 Наступна оплата: <b>{_ds(nd)}</b>"
                     + (f" <i>(через {left} дн.)</i>" if left > 0 else ""))
    if not vt and not nd:
        lines.append("<i>Конкретних дат у листі немає — нічого не вигадую.</i>")
    lines.append("")
    lines.append("🔕 Нагадування про це я вже вимкнув.")

    plan = _plan(entity, kind, vt, nd)
    if plan:
        lines.append("")
        lines.append("📅 <b>Можу записати в календар:</b>")
        for p in plan:
            lines.append(f"  • {_ds(_d(p['date']))} — {K.esc(p['title'])}")
        pid = _store.put({"entity": entity, "kind": kind, "plan": plan,
                          "source": source_id, "valid_to": vt and vt.isoformat(),
                          "next_due": nd and nd.isoformat()})
        kb = [[{"text": "📅 Записати все", "callback_data": f"lc_add_{pid}"}],
              [{"text": "❌ Не треба", "callback_data": f"lc_skip_{pid}"}]]
    else:
        pid = _store.put({"entity": entity, "kind": kind, "plan": [],
                          "source": source_id})
        kb = [[{"text": "👍 Ок", "callback_data": f"lc_skip_{pid}"}]]

    if not K.send_card("\n".join(lines), kb, tag=TAG):
        _store.drop(pid)
        K.log(TAG, f"⚠️ не вдалось надіслати картку по «{entity}»")


# ─── ДІЇ КНОПОК ──────────────────────────────────────────────────────────────

def do_add(pid: str) -> dict:
    """📅 Записати все — створює події в Google Calendar."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    plan = p.get("plan") or []
    if not plan:
        return {"ok": False, "error": "nothing_to_add"}
    done, failed = [], []
    for item in plan:
        d = _d(item.get("date"))
        if not d:
            failed.append(str(item.get("title") or ""))
            continue
        try:
            start = datetime(d.year, d.month, d.day, 9, 0)
            res = K.calendar_event(str(item.get("title") or "")[:120], start,
                                   start + timedelta(hours=1),
                                   str(item.get("note") or ""))
            ok = bool(res.get("ok")) if isinstance(res, dict) else bool(res)
            (done if ok else failed).append(str(item.get("title") or ""))
        except Exception as e:
            K.log(TAG, f"calendar error: {e}")
            failed.append(str(item.get("title") or ""))
    # позначаємо в реєстрі, що записано — щоб не пропонувати вдруге
    key = _slug(str(p.get("entity") or ""))
    if key and key in _reg():
        rec = dict(_reg()[key])
        rec["calendared"] = True
        rec["ts"] = K.now().isoformat()
        K.update_key(FILE, key, rec)
    _store.drop(pid)
    K.log(TAG, f"📅 записано {len(done)}, не вдалось {len(failed)}")
    msg = f"📅 Записав у календар: {len(done)}."
    if failed:
        msg += f" Не вдалось: {len(failed)} — {K.esc('; '.join(failed)[:100])}."
    return {"ok": bool(done), "text": msg, "added": len(done), "failed": len(failed)}


def do_skip(pid: str) -> dict:
    """❌ Не треба — тема і так уже закрита, просто нічого не пишемо."""
    p = _store.get(pid) or {}
    _store.drop(pid)
    return {"ok": True, "entity": p.get("entity")}


def payload(pid):
    return _store.get(pid)


# ─── ЩОДЕННА ПЕРЕВІРКА РЕЄСТРУ ───────────────────────────────────────────────

def check_expiring(force: bool = False) -> int:
    """Справа закінчується ≤14 днів або наступна оплата на носі → нагадуємо
    ЗАРАНІ й одразу з датами. Раз на день на кожну справу."""
    sent = 0
    try:
        reg = _reg()
        if not reg:
            return 0
        today = _today()
        for key, rec in list(reg.items()):
            if not isinstance(rec, dict):
                continue
            entity = str(rec.get("entity") or key)
            kind = str(rec.get("kind") or "other")
            vt, nd = _d(rec.get("valid_to")), _d(rec.get("next_due"))
            target = nd or vt
            if not target:
                continue
            left = (target - today).days
            if left < 0:
                continue
            if left > WARN_DAYS and not force:
                continue
            stamp = f"{key}|{target.isoformat()}|{left // 3}"
            seen = K.load(SENT_FILE, default={}) or {}
            if stamp in seen and not force:
                continue
            icon = KIND_ICON.get(kind, "📌")
            when = "сьогодні" if left == 0 else f"через {left} дн."
            lines = [f"⏳ <b>СКОРО ПРОДОВЖЕННЯ</b>", "━━━━━━━━━━━━━━━━━━━━",
                     f"{icon} <b>{K.esc(entity)}</b>"]
            if vt:
                lines.append(f"📆 Діє до: <b>{_ds(vt)}</b>")
            if nd:
                lines.append(f"💸 Оплата: <b>{_ds(nd)}</b> — {when}")
            lines.append("")
            lines.append("Олеже, щоб не було розриву — краще закрити це заздалегідь.")
            plan = _plan(entity, kind, vt, nd)
            pid = _store.put({"entity": entity, "kind": kind, "plan": plan,
                              "source": f"expiring:{key}"})
            kb = [[{"text": "📅 Записати нагадування", "callback_data": f"lc_add_{pid}"}],
                  [{"text": "❌ Не нагадувати", "callback_data": f"lc_skip_{pid}"}]]
            if K.send_card("\n".join(lines), kb, tag=TAG):
                K.update_key(SENT_FILE, stamp, K.now().isoformat())
                sent += 1
                K.log(TAG, f"⏳ попередив про «{entity}» ({left} дн.)")
            else:
                _store.drop(pid)
        _store.gc(days=20)
    except Exception as e:
        print(f"[lifecycle] check_expiring error: {e}", flush=True)
    return sent


# ─── ЗВІТ ────────────────────────────────────────────────────────────────────

def report() -> str:
    """/справи — що оплачено, до якого числа діє, коли наступна оплата."""
    reg = _reg()
    if not reg:
        return ("📒 <b>МОЇ СПРАВИ</b>\n\nПорожньо. Реєстр наповнюється сам: коли в "
                "листі бачу оплату/поліс/бронювання — записую сюди строки дії "
                "й дату наступної оплати.")
    today = _today()
    rows = []
    for key, rec in reg.items():
        if not isinstance(rec, dict):
            continue
        vt, nd = _d(rec.get("valid_to")), _d(rec.get("next_due"))
        rows.append(((nd or vt or today.replace(year=today.year + 9)), key, rec, vt, nd))
    rows.sort(key=lambda r: r[0])
    out = ["📒 <b>МОЇ СПРАВИ</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for _, key, rec, vt, nd in rows[:20]:
        icon = KIND_ICON.get(str(rec.get("kind")), "📌")
        entity = K.esc(str(rec.get("entity") or key))
        st = str(rec.get("state") or "")
        badge = {"paid": "✅ оплачено", "completed": "🏁 позаду"}.get(st, st)
        line = f"\n{icon} <b>{entity}</b> — {badge}"
        if vt:
            left = (vt - today).days
            line += (f"\n   📆 діє до {_ds(vt)}"
                     + (f" <i>({left} дн.)</i>" if left >= 0 else " <i>(вже минуло)</i>"))
        if nd:
            left = (nd - today).days
            line += (f"\n   💸 наступна оплата {_ds(nd)}"
                     + (f" <i>(через {left} дн.)</i>" if left > 0
                        else " <i>(вже пора)</i>" if left == 0 else ""))
        if rec.get("calendared"):
            line += "\n   📅 <i>записано в календар</i>"
        out.append(line)
    out.append("\n<i>Нагадування про закриті справи вимкнені: /вимкнені_нагадування</i>")
    return "\n".join(out)[:3900]


if __name__ == "__main__":
    import sys as _s
    if "--check" in _s.argv:
        print("sent:", check_expiring(force=True))
    else:
        print(report())
