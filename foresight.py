# -*- coding: utf-8 -*-
"""foresight.py — бот дивиться ВПЕРЕД і пропонує сам, на крок раніше.

Вимога Олега (03.09.2026):
  • спершу ВАЖЛИВЕ, потім дрібне і реклама;
  • бачить поїздку (напр. 07.09 Малага) → сам дає список речей, погоду там,
    що варто зробити до вильоту і що варто відвідати;
  • бачить блок нічних змін → сам зводить його з незакритими справами
    («у пʼятницю забрати взуття») і пропонує реальне вікно;
  • листи: спершу важливі, реклама — одним рядком у кінці;
  • ЖОДНОГО запису нікуди без питання — усе через calgate/askme,
    дія виконується тільки після натискання кнопки.

Дедуп: кожна тема брифиться один раз (ключ у foresight_state.json).
Частота: не частіше ніж раз на 150 хв, і лише коли реально є що сказати.
"""

from datetime import datetime, timedelta

import ai_kit as K

TAG = "foresight"
STATE = "foresight_state.json"
RATE = "foresight_rate.json"
MIN_GAP_MIN = 150

# Скільки днів наперед дивимось
TRIP_HORIZON = 21
SHIFT_HORIZON = 10

# Ознаки поїздки в назві події
_TRAVEL = ("поїздк", "подорож", "виліт", "переліт", "рейс", "flight",
           "аеропорт", "airport", "готель", "hotel", "відпустк", "vacation",
           "trip", "travel", "їду в", "їдемо в", "летимо", "лечу",
           "check-in", "чек-ін", "booking", "airbnb", "ryanair", "wizz")

# Міста, які розпізнаємо без AI (далі — AI-екстракція)
_CITIES = {
    "малаг": "Málaga", "malag": "Málaga", "барселон": "Barcelona",
    "barcelon": "Barcelona", "мадрид": "Madrid", "madrid": "Madrid",
    "прага": "Prague", "prague": "Prague", "praha": "Prague",
    "відень": "Vienna", "vienna": "Vienna", "wien": "Vienna",
    "будапешт": "Budapest", "budapest": "Budapest", "краків": "Krakow",
    "krakow": "Krakow", "варшав": "Warsaw", "warsaw": "Warsaw",
    "київ": "Kyiv", "kyiv": "Kyiv", "львів": "Lviv", "lviv": "Lviv",
    "ужгород": "Uzhhorod", "братислав": "Bratislava",
    "bratislav": "Bratislava", "кошиц": "Košice", "kosice": "Košice",
    "рим": "Rome", "rome": "Rome", "мілан": "Milan", "milan": "Milan",
    "париж": "Paris", "paris": "Paris", "лондон": "London",
    "london": "London", "берлін": "Berlin", "berlin": "Berlin",
    "стамбул": "Istanbul", "istanbul": "Istanbul", "дубай": "Dubai",
    "dubai": "Dubai", "тенериф": "Tenerife", "лісабон": "Lisbon",
    "lisbon": "Lisbon", "валенсі": "Valencia", "valencia": "Valencia",
    "аліканте": "Alicante", "alicante": "Alicante",
}

# Профіль Олега для AI — щоб порада була його, а не з інтернету
_PROFILE = ("Олег Новосадов, Кошице (Словаччина). Працює на Minebea Mitsumi "
            "змінами: рання 06:00-18:00, нічна 18:00-06:00. Бігає, цілі: "
            "вага з 83-84 кг до 78 кг, фінансова незалежність, інвестиції "
            "(крипта BTC/ETH/AVAX/ONDO, InterFin), здоровий спосіб життя. "
            "Українець, пише українською.")


def _log(m):
    print("[" + TAG + "] " + str(m), flush=True)


def _now():
    return K.now()


# ─── ПАМʼЯТЬ ТЕМ (щоб не брифити двічі) ──────────────────────────────────────

def _state() -> dict:
    d = K.load(STATE, default={}) or {}
    return d if isinstance(d, dict) else {}


def _seen(key: str) -> bool:
    return bool(_state().get(str(key)))


def _mark(key: str, note: str = "") -> None:
    try:
        K.update_key(STATE, str(key), {
            "at": _now().isoformat(timespec="seconds"),
            "note": str(note)[:120],
        })
    except Exception as e:
        _log("state save: " + str(e))


# ─── ПОГОДА НА МІСЦІ (open-meteo, без ключа) ─────────────────────────────────

_WMO_BAD = (51, 53, 55, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99)


def _http_json(url: str, params: dict):
    try:
        import httpreq
        r = httpreq.get(url, params=params, timeout=12)
        if not r.ok:
            return None
        return r.json()
    except Exception as e:
        _log("http: " + str(e))
        return None


def _geocode(city: str):
    j = _http_json("https://geocoding-api.open-meteo.com/v1/search",
                   {"name": city, "count": 1, "language": "uk"})
    try:
        it = (j or {}).get("results") or []
        if not it:
            return None
        return {"lat": it[0]["latitude"], "lon": it[0]["longitude"],
                "name": it[0].get("name") or city,
                "country": it[0].get("country") or ""}
    except Exception:
        return None


def weather_for(city: str, d_from, d_to):
    """Прогноз на місці поїздки. None, якщо API недоступний."""
    g = _geocode(city)
    if not g:
        return None
    # open-meteo дає прогноз ~16 днів наперед
    j = _http_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": g["lat"], "longitude": g["lon"],
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
        "timezone": "auto",
        "start_date": d_from.strftime("%Y-%m-%d"),
        "end_date": d_to.strftime("%Y-%m-%d"),
    })
    try:
        d = (j or {}).get("daily") or {}
        days = d.get("time") or []
        if not days:
            return None
        tmax = d.get("temperature_2m_max") or []
        tmin = d.get("temperature_2m_min") or []
        rain = d.get("precipitation_sum") or []
        codes = d.get("weathercode") or []
        hi = max([t for t in tmax if t is not None] or [0])
        lo = min([t for t in tmin if t is not None] or [0])
        wet = sum(1 for x in rain if (x or 0) >= 1.0)
        bad = sum(1 for c in codes if c in _WMO_BAD)
        lines = []
        for i, day in enumerate(days[:8]):
            try:
                lab = datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m")
            except Exception:
                lab = str(day)
            mx = tmax[i] if i < len(tmax) else None
            mn = tmin[i] if i < len(tmin) else None
            rn = rain[i] if i < len(rain) else 0
            piece = lab + ": " + str(round(mx or 0)) + "°/" + \
                str(round(mn or 0)) + "°"
            if (rn or 0) >= 1.0:
                piece += " 🌧" + str(round(rn, 1)) + "мм"
            lines.append(piece)
        return {"city": g["name"], "country": g["country"], "hi": round(hi),
                "lo": round(lo), "wet_days": wet, "bad": bad,
                "lines": lines}
    except Exception as e:
        _log("weather parse: " + str(e))
        return None


# ─── ЧИТАННЯ КАЛЕНДАРЯ ──────────────────────────────────────────────────────

def _ev_title(ev) -> str:
    if isinstance(ev, dict):
        return str(ev.get("summary") or ev.get("title") or "")[:140]
    return str(ev)[:140]


def _is_travel(title: str) -> bool:
    low = str(title or "").lower()
    if any(w in low for w in _TRAVEL):
        return True
    # «Малага», «Барселона» в назві — теж поїздка
    return any(c in low for c in _CITIES)


def _city_of(title: str):
    low = str(title or "").lower()
    for frag, name in _CITIES.items():
        if frag in low:
            return name
    # Не вгадали словником — питаємо AI (один раз, результат кешується)
    try:
        j = K.gemini_json(
            "Назва події з календаря: « " + str(title)[:120] + " ». "
            "Якщо це поїздка — поверни JSON {\"city\": \"місто латиницею\", "
            "\"country\": \"країна\"}. Якщо це НЕ поїздка — {\"city\": \"\"}.",
            max_tokens=120, temperature=0.1, tag=TAG) or {}
        c = str(j.get("city") or "").strip()
        return c or None
    except Exception:
        return None


def find_trips(days: int = TRIP_HORIZON) -> list:
    """Поїздки в календарі на N днів наперед."""
    out, seen = [], set()
    for off in range(0, days + 1):
        try:
            evs = K.events_for_day(off) or []
        except Exception:
            continue
        for ev in evs:
            t = _ev_title(ev)
            if not t or not _is_travel(t):
                continue
            try:
                import askme as A
                if A.is_promo(t) or A._is_tracker(t):
                    continue
            except Exception:
                pass
            city = _city_of(t)
            if not city:
                continue
            day = (_now() + timedelta(days=off)).date()
            k = city.lower() + "|" + day.strftime("%Y-%m-%d")
            if k in seen:
                continue
            seen.add(k)
            out.append({"city": city, "title": t, "offset": off, "date": day})
    return out


def _trip_span(trip: dict, trips: list):
    """Скільки днів триває поїздка — за сусідніми подіями того ж міста."""
    same = sorted([t["offset"] for t in trips
                   if t["city"].lower() == trip["city"].lower()])
    if len(same) < 2:
        return trip["offset"], trip["offset"] + 3
    return same[0], same[-1]


# ─── БРИФ ПОЇЗДКИ ───────────────────────────────────────────────────────────

def _trip_brief() -> str:
    trips = find_trips()
    if not trips:
        return ""
    trip = sorted(trips, key=lambda t: t["offset"])[0]
    if trip["offset"] > 14:
        return ""
    key = "trip|" + trip["city"].lower() + "|" + trip["date"].strftime("%Y%m%d")
    if _seen(key):
        return ""
    o1, o2 = _trip_span(trip, trips)
    d1 = (_now() + timedelta(days=o1)).date()
    d2 = (_now() + timedelta(days=o2)).date()
    nights = max(1, (d2 - d1).days)
    w = weather_for(trip["city"], d1, d2)
    wtxt = "прогноз недоступний"
    if w:
        wtxt = (str(w["lo"]) + "…" + str(w["hi"]) + "°C, дощових днів: " +
                str(w["wet_days"]) + ". По днях: " + "; ".join(w["lines"]))
    try:
        shifts = K.shift_map(SHIFT_HORIZON) or {}
    except Exception:
        shifts = {}
    sh = ", ".join(d + "=" + v for d, v in sorted(shifts.items())[:8])
    prompt = (
        "Ти особистий асистент Олега. " + _PROFILE + "\n\n"
        "У календарі поїздка: « " + trip["title"][:120] + " ». Місто: " +
        trip["city"] + ". Дати: " + d1.strftime("%d.%m") + "–" +
        d2.strftime("%d.%m") + " (" + str(nights) + " дн.). Через " +
        str(trip["offset"]) + " дн.\n"
        "Погода на місці: " + wtxt + "\n"
        "Зміни Олега на тижні: " + (sh or "невідомо") + "\n\n"
        "Напиши українською БРИФ ПОЇЗДКИ — коротко, конкретно, по фактах "
        "погоди вище (не вигадуй температур). Формат саме такий:\n"
        "🧳 <b>ЩО ВЗЯТИ</b> — 8-12 пунктів одним рядком кожен, прив'язані до "
        "погоди й тривалості (одяг, взуття, зарядки/перехідник, документи, "
        "аптечка, кросівки якщо є де бігати).\n"
        "🌤 <b>ПОГОДА</b> — 2-3 речення висновку: що вдягати, чи потрібна "
        "парасоля/куртка.\n"
        "📍 <b>ЩО ВАРТО ВІДВІДАТИ</b> — 5 місць з одним рядком чому саме.\n"
        "🍽 <b>СПРОБУВАТИ</b> — 3 місцеві страви/напої.\n"
        "✅ <b>ДО ВИЛЬОТУ</b> — 4 конкретні дії з датами (документи, онлайн "
        "чек-ін, гроші/картка, транспорт з дому в аеропорт з урахуванням "
        "змін).\n"
        "🏃 <b>РЕЖИМ</b> — 2 речення: як не втратити біг і не збити вагу "
        "(ціль 78 кг) у поїздці.\n\n"
        "ДАНІ: температури вище — СВІЖИЙ прогноз open-meteo, отриманий щойно "
        "саме на ці дати, подавай їх як актуальні. Жодних інших цифр "
        "(вага, кроки, сон, пульс, курси крипти) не наводь — про них у цьому "
        "брифі не пишемо взагалі.\n"
        "Тон: теплий, як друг-помічник. Емодзі в заголовках. HTML-теги лише "
        "<b>. Без вступу і без підпису. До 420 слів.")
    body = ""
    try:
        body = K.gemini_text(prompt, max_tokens=1500, temperature=0.75,
                             tag=TAG) or ""
    except Exception as e:
        _log("gemini: " + str(e))
    if not body.strip():
        body = _trip_fallback(trip, d1, d2, nights, w)
    head = ("✈️ <b>ПОЇЗДКА: " + K.esc(trip["city"]) + "</b> — через " +
            str(trip["offset"]) + " дн. (" + d1.strftime("%d.%m") + "–" +
            d2.strftime("%d.%m") + ")\n\n")
    ok = False
    try:
        ok = K.send_card(head + body, tag="MSG_FORESIGHT_TRIP")
    except Exception as e:
        _log("send: " + str(e))
    if not ok:
        return ""
    _mark(key, "trip brief " + trip["city"])
    # Нагадування збиратись — ЛИШЕ після його «так»
    try:
        import askme as A
        when = (_now() + timedelta(days=max(0, o1 - 1))).replace(
            hour=18, minute=0, second=0, microsecond=0)
        A.ask("🔔 Нагадати зібрати речі в " + trip["city"] + " — " +
              when.strftime("%d.%m о %H:%M") + "?",
              kind="remind", key="trippack|" + key,
              meta={"summary": "🧳 Зібрати речі: " + trip["city"],
                    "start": when.isoformat(),
                    "desc": "Бриф поїздки вже надіслано."},
              tag="MSG_FORESIGHT_TRIP_ASK")
    except Exception as e:
        _log("ask skip: " + str(e))
    return "trip:" + trip["city"]


def _trip_fallback(trip, d1, d2, nights, w) -> str:
    """Без AI — усе одно даємо користь із реальних даних."""
    lines = ["🧳 <b>ЩО ВЗЯТИ</b>"]
    if w:
        warm = w["hi"] >= 24
        lines += ["• одяг на " + str(w["lo"]) + "…" + str(w["hi"]) + "°C",
                  "• " + ("футболки, шорти, окуляри, крем від сонця"
                          if warm else "кофта, вітровка, закрите взуття")]
        if w["wet_days"]:
            lines.append("• парасоля/дощовик — дощових днів: " +
                         str(w["wet_days"]))
    lines += ["• документи (паспорт/ID), картка + трохи готівки",
              "• зарядки, павербанк, перехідник",
              "• аптечка: обезболююче, пластир, засіб від шлунку",
              "• кросівки — щоб не випадати з бігу (" + str(nights) + " дн.)"]
    if w:
        lines += ["", "🌤 <b>ПОГОДА</b>", "• " + "; ".join(w["lines"])]
    lines += ["", "✅ <b>ДО ВИЛЬОТУ</b>",
              "• онлайн чек-ін за 24-48 год",
              "• перевірити термін дії документів",
              "• спланувати дорогу в аеропорт з урахуванням зміни",
              "• зняти/поповнити картку на місцеві витрати"]
    return "\n".join(lines)


# ─── БРИФ БЛОКУ НІЧНИХ ЗМІН + НЕЗАКРИТІ СПРАВИ ──────────────────────────────

def _open_items(limit: int = 8) -> list:
    """Незакриті справи: нагадування наперед + нотатки Олега."""
    out = []
    try:
        rem = K.load("reminders.json", default=[]) or []
        if isinstance(rem, list):
            for r in rem:
                if not isinstance(r, dict) or r.get("sent"):
                    continue
                t = str(r.get("text") or "")
                import re as _re
                t = _re.sub(r"<[^>]+>", " ", t)
                t = " ".join(t.split())[:90]
                if t:
                    out.append(str(r.get("datetime_utc") or "")[:16] +
                               " — " + t)
    except Exception as e:
        _log("reminders: " + str(e))
    try:
        n = K.load("ai_notes.json", default={}) or {}
        for it in (n.get("notes") or [])[-6:]:
            if isinstance(it, dict):
                s = str(it.get("text") or it.get("title") or "")[:90]
            else:
                s = str(it)[:90]
            if s:
                out.append("нотатка — " + s)
    except Exception:
        pass
    return out[:limit]


def _night_block(shifts: dict):
    """Найближчий блок з ≥2 нічних поспіль: (start_date, кількість)."""
    days = sorted(shifts.items())
    run_start, run = None, 0
    best = None
    for day, kind in days:
        if kind == "night":
            if run == 0:
                run_start = day
            run += 1
            if run >= 2 and best is None:
                best = (run_start, run)
            elif best and run_start == best[0]:
                best = (run_start, run)
        else:
            if best:
                break
            run, run_start = 0, None
    return best


def _shift_brief() -> str:
    try:
        shifts = K.shift_map(SHIFT_HORIZON) or {}
    except Exception as e:
        _log("shift_map: " + str(e))
        return ""
    if not shifts:
        return ""
    blk = _night_block(shifts)
    if not blk:
        return ""
    start, count = blk
    try:
        sd = datetime.strptime(start, "%Y-%m-%d").date()
    except Exception:
        return ""
    left = (sd - _now().date()).days
    if left < 0 or left > 3:
        return ""
    key = "nights|" + start + "|" + str(count)
    if _seen(key):
        return ""
    items = _open_items()
    free = [d for d, v in sorted(shifts.items()) if v == "free"
            and d >= _now().strftime("%Y-%m-%d")][:3]
    prompt = (
        "Ти особистий асистент Олега. " + _PROFILE + "\n\n"
        "Графік змін на дні наперед: " +
        ", ".join(d + "=" + v for d, v in sorted(shifts.items())) + "\n"
        "Починається блок НІЧНИХ: " + str(count) + " поспіль з " +
        sd.strftime("%d.%m") + " (через " + str(left) + " дн.).\n"
        "Вільні дні: " + (", ".join(free) or "немає") + "\n"
        "Незакриті справи Олега:\n" +
        ("\n".join("- " + i for i in items) if items else "- (порожньо)") +
        "\n\nНапиши українською короткий ПЛАН під цей блок нічних. Формат:\n"
        "🌙 <b>БЛОК НІЧНИХ</b> — 2 речення: що це означає для тижня.\n"
        "📌 <b>ЗРОБИТИ ДО НІЧНИХ</b> — конкретні справи зі списку вище, "
        "кожна з реальним днем і часом (враховуй, що в нічні дні вільний "
        "тільки ранок до 14:00). Якщо справ немає — запропонуй 2 корисні "
        "(їжа на зміни, сон, аптека).\n"
        "😴 <b>СОН</b> — 3 пункти: коли лягати/вставати між нічними.\n"
        "🏃 <b>БІГ І ВАГА</b> — 2 пункти: коли реально бігти в цьому блоці, "
        "щоб не втратити прогрес до 78 кг.\n\n"
        "ДАНІ: графік змін і список справ вище — актуальні, подавай їх як "
        "актуальні. Жодних цифр ваги, кроків, сну, пульсу чи крипти не "
        "наводь — у цьому плані їх немає.\n"
        "Тон: як друг, що вже все прикинув. Емодзі. HTML лише <b>. "
        "До 220 слів. Без вступу.")
    body = ""
    try:
        body = K.gemini_text(prompt, max_tokens=900, temperature=0.7,
                             tag=TAG) or ""
    except Exception as e:
        _log("gemini: " + str(e))
    if not body.strip():
        body = ("🌙 <b>БЛОК НІЧНИХ</b>\n• " + str(count) + " нічних поспіль з " +
                sd.strftime("%d.%m") + " — вільний тільки ранок до 14:00.\n" +
                ("\n📌 <b>ЗРОБИТИ ДО НІЧНИХ</b>\n" +
                 "\n".join("• " + i for i in items) if items else "") +
                "\n\n😴 <b>СОН</b>\n• ляж до 10:00 після зміни, встань 16:00.")
    ok = False
    try:
        ok = K.send_card(body, tag="MSG_FORESIGHT_SHIFTS")
    except Exception as e:
        _log("send: " + str(e))
    if not ok:
        return ""
    _mark(key, "nights " + start)
    # Найближче вікно під справи — питаємо, чи ставити нагадування
    if items:
        try:
            import askme as A
            when = (_now() + timedelta(days=max(0, left - 1))).replace(
                hour=10, minute=0, second=0, microsecond=0)
            first = items[0].split(" — ")[-1][:70]
            A.ask("🔔 До нічних лишилось " + str(max(0, left)) +
                  " дн. Нагадати « " + first + " » " +
                  when.strftime("%d.%m о %H:%M") + "?",
                  kind="remind", key="nightprep|" + key,
                  meta={"summary": first, "start": when.isoformat(),
                        "desc": "Вікно перед блоком нічних змін."},
                  tag="MSG_FORESIGHT_SHIFTS_ASK")
        except Exception as e:
            _log("ask skip: " + str(e))
    return "nights:" + start


# ─── ЛИСТИ: ВАЖЛИВІ СПЕРШУ, РЕКЛАМА ОДНИМ РЯДКОМ ────────────────────────────

def _mail_brief() -> str:
    key = "mail|" + _now().strftime("%Y-%m-%d")
    if _seen(key):
        return ""
    try:
        import monitor as M
        data = M.get_emails() or {}
    except Exception as e:
        _log("emails: " + str(e))
        return ""
    items = data.get("items") if isinstance(data, dict) else None
    if not items:
        return ""
    try:
        import askme as A
        is_promo = A.is_promo
    except Exception:
        def is_promo(_x):
            return False
    важливі, реклама = [], 0
    for it in items:
        if not isinstance(it, dict):
            continue
        subj = str(it.get("subject") or "")[:110]
        frm = str(it.get("from") or it.get("sender") or "")[:60]
        if is_promo(subj + " " + frm):
            реклама += 1
            continue
        важливі.append({"from": frm, "subject": subj,
                        "starred": bool(it.get("starred"))})
    if not важливі:
        return ""
    важливі = sorted(важливі, key=lambda x: (not x["starred"],))[:5]
    lst = "\n".join("- від " + i["from"] + ": " + i["subject"] +
                    (" (★)" if i["starred"] else "") for i in важливі)
    prompt = (
        "Ти асистент Олега. " + _PROFILE + "\n\n"
        "ВАЖЛИВІ листи в скриньці (реклама вже відфільтрована, її " +
        str(реклама) + " шт.):\n" + lst + "\n\n"
        "Напиши українською короткий розбір: спершу найважливіше. Для КОЖНОГО "
        "листа один рядок «📬 від кого → що це і що з цим робити». Потім "
        "рядок «🎯 ЗАРАЗ:» з ОДНІЄЮ дією, яку варто зробити першою. "
        "В кінці рядок «🧹 Реклама: " + str(реклама) + " — можна пропустити». "
        "HTML лише <b>. До 160 слів. Без вступу.")
    body = ""
    try:
        body = K.gemini_text(prompt, max_tokens=700, temperature=0.6,
                             tag=TAG) or ""
    except Exception as e:
        _log("gemini: " + str(e))
    if not body.strip():
        body = ("📬 <b>ВАЖЛИВІ ЛИСТИ</b>\n" +
                "\n".join("• " + i["from"] + " → " + i["subject"]
                          for i in важливі) +
                "\n\n🧹 Реклама: " + str(реклама) + " — можна пропустити.")
    ok = False
    try:
        ok = K.send_card("📥 <b>СПЕРШУ ВАЖЛИВЕ</b>\n\n" + body,
                         tag="MSG_FORESIGHT_MAIL")
    except Exception as e:
        _log("send: " + str(e))
    if not ok:
        return ""
    _mark(key, "mail brief " + str(len(важливі)))
    return "mail:" + str(len(важливі))


# ─── ТОЧКА ВХОДУ ────────────────────────────────────────────────────────────

def tick(force: bool = False) -> str:
    """Один прохід. Повертає що саме надіслано ('' — нічого)."""
    if not force and not K.rate_ok(RATE, MIN_GAP_MIN):
        return ""
    for fn in (_trip_brief, _shift_brief, _mail_brief):
        try:
            done = fn()
        except Exception as e:
            _log(fn.__name__ + " error: " + str(e))
            done = ""
        if done:
            try:
                K.rate_mark(RATE)
            except Exception:
                pass
            _log("надіслано → " + done)
            return done
    return ""


def report(limit: int = 12) -> str:
    """Для /вперед — що бот уже побачив і про що брифив."""
    st = _state()
    lines = ["🔭 <b>ФОРСАЙТ</b> — що бачу наперед\n"]
    try:
        trips = find_trips()
    except Exception:
        trips = []
    if trips:
        for t in trips[:5]:
            lines.append("✈️ " + t["city"] + " — " + t["date"].strftime("%d.%m") +
                         " (через " + str(t["offset"]) + " дн.)")
    else:
        lines.append("✈️ поїздок у календарі на 3 тижні не бачу")
    try:
        shifts = K.shift_map(SHIFT_HORIZON) or {}
        blk = _night_block(shifts)
        if blk:
            lines.append("🌙 нічних поспіль: " + str(blk[1]) + " з " + blk[0])
        free = [d for d, v in sorted(shifts.items()) if v == "free"]
        if free:
            lines.append("🟢 вільні дні: " + ", ".join(free[:4]))
    except Exception:
        pass
    items = _open_items(5)
    if items:
        lines.append("\n📌 <b>Незакрите</b>")
        lines += ["• " + i for i in items]
    if st:
        lines.append("\n🗂 <b>Уже брифив</b>")
        for k, v in list(st.items())[-limit:]:
            at = str((v or {}).get("at") or "")[:16] if isinstance(v, dict) else ""
            lines.append("• " + k + (" — " + at if at else ""))
    return "\n".join(lines)
