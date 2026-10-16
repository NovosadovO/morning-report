"""
astro_journal.py — легкий трекер "що збіглось, що ні" між астро-прогнозами
бота і реальним життям (запит Олега, 20.10): "легкий трекер що збіглось, що
ні між транзитами й реальними подіями в твоєму житті, щоб бачити власну
статистику, а не вірити на слово".

Дані: astro_journal.json — список записів:
    {id, date, slot, theme, text, status: pending|ok|no|unsure,
     created, answered_at, asked}

Життєвий цикл одного запису:
1. monitor.py після кожного АСТРО-AI-розбору (2х/день, слоти 09 і 18)
   викликає log_prediction(text, slot) — зберігає тему дня.
2. Наступного дня (коли вже можна оцінити, чи збіглось — MIN_AGE_HOURS)
   ask_tick() (викликається з циклу в monitor_loop.py) раз на день пропонує
   Олегу оцінити запис кнопками.
3. bot.py приймає callback aj_ok_<id> / aj_no_<id> / aj_unsure_<id> →
   record_feedback().
4. Команда /астростат (bot.py) показує stats_block() — скільки збіглось,
   скільки ні, % за весь час і за останні 30 днів — ЙОГО власна статистика,
   не чиясь загальна думка про астрологію.
"""

import re
from datetime import datetime

import ai_kit as K

TAG = "astro_journal"
FILE = "astro_journal.json"
ASK_STATE_FILE = "astro_journal_ask.json"
KEEP = 300
ASK_WINDOW = (9, 21)      # питаємо тільки вдень/ввечері, не вночі
MIN_AGE_HOURS = 14        # запис має "відстоятись" — щоб день уже прожито
ASK_GAP_MIN = 20 * 60     # не частіше раз на ~20 год


def _now():
    return K.now()


def _load() -> list:
    return K.load(FILE, default=[]) or []


def _save(items: list):
    K.save(FILE, items[-KEEP:])


def _extract_theme(text: str, limit: int = 180) -> str:
    """Короткий заголовок — перше змістовне речення/рядок аналізу."""
    t = re.sub(r"<[^>]+>", "", text or "").strip()
    for sep in ["\n\n", "\n"]:
        if sep in t:
            first = t.split(sep)[0].strip()
            if len(first) >= 20:
                t = first
                break
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:limit] + "…") if len(t) > limit else t


def log_prediction(text: str, slot: str = "") -> str:
    """Зберігає денний астро-розбір як запис журналу. Дедуп — не більше
    одного запису на (дата, slot). Повертає id нового запису або ""."""
    if not text:
        return ""
    now = _now()
    date = now.strftime("%Y-%m-%d")
    items = _load()
    for it in items:
        if it.get("date") == date and it.get("slot") == slot:
            return ""  # вже є запис на цей слот сьогодні — не дублюємо
    entry_id = f"{date}_{slot or 'x'}_{int(now.timestamp())}"
    items.append({
        "id": entry_id, "date": date, "slot": slot,
        "theme": _extract_theme(text), "text": text[:2000],
        "status": "pending", "created": now.isoformat(timespec="seconds"),
        "answered_at": None, "asked": False,
    })
    _save(items)
    K.log(TAG, f"log_prediction: {entry_id}")
    return entry_id


def _pending_to_ask():
    """Найстаріший неоцінений запис, якому вже >= MIN_AGE_HOURS і про який
    ще не питали — щоб не питати про сьогоднішній прогноз, який ще триває."""
    now = _now()
    candidates = []
    for it in _load():
        if it.get("status") != "pending" or it.get("asked"):
            continue
        try:
            created = datetime.fromisoformat(it["created"])
        except Exception:
            continue
        if (now - created).total_seconds() / 3600 >= MIN_AGE_HOURS:
            candidates.append(it)
    candidates.sort(key=lambda x: x["created"])
    return candidates[0] if candidates else None


def ask_tick(force: bool = False) -> bool:
    """Раз на день (ворота по стейт-файлу), якщо є що оцінити — питає Олега
    кнопками, чи збіглось учорашнє (чи раніше) астро-спостереження."""
    now = _now()
    if not force and not (ASK_WINDOW[0] <= now.hour <= ASK_WINDOW[1]):
        return False
    if not force and not K.rate_ok(ASK_STATE_FILE, ASK_GAP_MIN):
        return False
    entry = _pending_to_ask()
    if not entry:
        return False
    K.rate_mark(ASK_STATE_FILE)

    items = _load()
    for it in items:
        if it.get("id") == entry["id"]:
            it["asked"] = True
    _save(items)

    text = (f"🔭 <b>Astro-журнал: чи збіглось?</b>\n\n"
            f"{entry['date']}: «{entry['theme']}»\n\n"
            f"Збіглось з реальністю того дня?")
    kb = [[
        {"text": "✅ Збіглось", "callback_data": f"aj_ok_{entry['id']}"},
        {"text": "❌ Не збіглось", "callback_data": f"aj_no_{entry['id']}"},
    ], [
        {"text": "🤷 Не певен", "callback_data": f"aj_unsure_{entry['id']}"},
    ]]
    return K.send_card(text, kb, tag=TAG)


def record_feedback(entry_id: str, verdict: str) -> bool:
    """verdict: 'ok'|'no'|'unsure'."""
    items = _load()
    found = False
    for it in items:
        if it.get("id") == entry_id:
            it["status"] = verdict
            it["answered_at"] = _now().isoformat(timespec="seconds")
            found = True
            break
    if found:
        _save(items)
    return found


def get_entry(entry_id: str):
    for it in _load():
        if it.get("id") == entry_id:
            return it
    return None


def stats(days: int = None) -> dict:
    items = _load()
    now = _now()
    if days:
        def _in_range(it):
            try:
                d = datetime.strptime(it["date"], "%Y-%m-%d")
            except Exception:
                return False
            return (now - d).days <= days
        items = [it for it in items if _in_range(it)]
    counts = {"ok": 0, "no": 0, "unsure": 0, "pending": 0}
    for it in items:
        status = it.get("status", "pending")
        counts[status] = counts.get(status, 0) + 1
    answered = counts["ok"] + counts["no"] + counts["unsure"]
    pct = round(counts["ok"] / answered * 100, 1) if answered else None
    return {"total": len(items), "answered": answered, "match_pct": pct, **counts}


def stats_block() -> str:
    all_time = stats()
    last30 = stats(30)
    lines = ["🔭 <b>Astro-журнал — твоя статистика</b>", ""]
    if all_time["answered"] == 0:
        lines.append("Поки немає оцінених записів — журнал щойно почав накопичуватись, "
                      "питатиму раз на день про минулі астро-розбори.")
    else:
        lines.append(f"За весь час: {all_time['answered']} оцінено "
                      f"(✅ {all_time['ok']} / ❌ {all_time['no']} / 🤷 {all_time['unsure']})"
                      + (f" — збіг {all_time['match_pct']}%" if all_time['match_pct'] is not None else ""))
        if last30["answered"] > 0:
            lines.append(f"За 30 днів: {last30['answered']} оцінено"
                          + (f" — збіг {last30['match_pct']}%" if last30['match_pct'] is not None else ""))
    if all_time.get("pending"):
        lines.append(f"\nЧекає твоєї оцінки: {all_time['pending']}")
    return "\n".join(lines)
