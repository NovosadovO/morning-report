"""
tzclock.py — єдиний «місцевий час» Кошице (Europe/Bratislava, CET/CEST).

БАГ (19.11): у ~200 місцях коду було зашито UTC+2 (літній час). З 25.10.2026
в Кошице UTC+1, тож бот показував час на годину вперед, вважав, що зміна
почалась/закінчилась не тоді, рахував «сьогодні» з 23:00 і створював події
в календарі на годину раніше. Тепер усе бере зсув звідси.

Правило ЄС реалізоване вручну (без zoneinfo/tzdata, щоб не залежати від
середовища): літній час — від останньої неділі березня 01:00 UTC до останньої
неділі жовтня 01:00 UTC. Зсув +1 год узимку, +2 влітку.

API:
    local_offset(at=None) -> timedelta   # at: None=зараз, aware→UTC, naive=UTC
    local_tz(at=None)     -> timezone    # фіксований tz із поточним зсувом
    iso_offset(naive_local) -> "+01:00"|"+02:00"  # для подій календаря
    local_now()           -> aware datetime у місцевому часі
"""
from datetime import datetime, timedelta, timezone


def _last_sunday(year, month):
    d = datetime(year, month, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _dst_bounds(year):
    start = _last_sunday(year, 3).replace(hour=1)
    end = _last_sunday(year, 10).replace(hour=1)
    return start, end


def local_offset(at=None):
    """Зсув місцевого часу від UTC у момент `at` (UTC-момент)."""
    if at is None:
        u = datetime.now(timezone.utc).replace(tzinfo=None)
    elif at.tzinfo is not None:
        u = at.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        u = at
    start, end = _dst_bounds(u.year)
    return timedelta(hours=2) if start <= u < end else timedelta(hours=1)


def local_tz(at=None):
    return timezone(local_offset(at))


def local_now():
    return datetime.now(timezone.utc) + local_offset()


def iso_offset(naive_local):
    """Зсув ISO для НАЇВНОГО місцевого часу (для створення подій).
    Невизначена година переходу трактується як літній час."""
    guess = naive_local - timedelta(hours=2)          # якщо це літо
    off = local_offset(guess)
    if off != timedelta(hours=2):
        off = local_offset(naive_local - timedelta(hours=1))
    secs = int(off.total_seconds())
    return "%s%02d:%02d" % ("+" if secs >= 0 else "-", abs(secs) // 3600,
                            abs(secs) % 3600 // 60)
