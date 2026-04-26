#!/usr/bin/env python3
"""
Трафік Кошіце через TomTom API.
Показує інциденти: затори, аварії, перекриття, ремонти.
"""

import os, json, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

TOMTOM_KEY = os.environ.get("TOMTOM_API_KEY", "cx2m0M3xY0hgqlwUeroRTKg4txk9HRph")

# Bbox Кошіце
BBOX = "21.20,48.65,21.30,48.75"

# Категорії інцидентів
ICON_MAP = {
    0:  ("⚠️",  "Невідомо"),
    1:  ("🚗",  "Аварія"),
    2:  ("🚗",  "Аварія"),
    3:  ("🚗",  "Аварія"),
    4:  ("🚗",  "Аварія"),
    5:  ("🔧",  "Ремонт"),
    6:  ("🐢",  "Затор"),
    7:  ("🐢",  "Затор"),
    8:  ("🚧",  "Перекрито"),
    9:  ("🔧",  "Ремонт"),
    10: ("⛅",  "Погодні умови"),
    11: ("⚠️",  "Небезпека"),
    14: ("🚧",  "Перекрито"),
}

MAGNITUDE_MAP = {
    0: "",
    1: "незначний",
    2: "помірний",
    3: "значний",
    4: "серйозний",
}


def get_incidents():
    """Повертає список інцидентів у Кошіце."""
    fields = "{incidents{type,properties{iconCategory,magnitudeOfDelay,events{description},from,to,delay,roadNumbers}}}"
    url = (
        f"https://api.tomtom.com/traffic/services/5/incidentDetails"
        f"?key={TOMTOM_KEY}"
        f"&bbox={BBOX}"
        f"&fields={urllib.parse.quote(fields)}"
        f"&language=sk-SK"
        f"&timeValidityFilter=present"
    )
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return data.get("incidents", [])
    except Exception as e:
        print(f"TomTom error: {e}")
        return []


def format_traffic_report():
    """Форматує звіт про трафік для Telegram."""
    incidents = get_incidents()
    if not incidents:
        return "🟢 <b>Трафік Кошіце</b>\nДоріг чисті, інцидентів немає."

    # Сортуємо за серйозністю (magnitude desc)
    incidents.sort(
        key=lambda x: x.get("properties", {}).get("magnitudeOfDelay", 0),
        reverse=True
    )

    lines = ["🚦 <b>Трафік Кошіце</b>\n"]

    # Групуємо по типу
    accidents  = []
    closures   = []
    slowdowns  = []
    works      = []
    other      = []

    for inc in incidents:
        props   = inc.get("properties", {})
        cat     = props.get("iconCategory", 0)
        mag     = props.get("magnitudeOfDelay", 0)
        from_   = props.get("from", "")
        to_     = props.get("to", "")
        delay   = props.get("delay")
        roads   = props.get("roadNumbers", [])
        events  = props.get("events", [])
        desc    = events[0].get("description", "") if events else ""

        emoji, label = ICON_MAP.get(cat, ("⚠️", "Інше"))
        mag_str = MAGNITUDE_MAP.get(mag, "")
        road_str = f" ({', '.join(roads)})" if roads else ""

        delay_str = ""
        if delay and delay > 0:
            mins = delay // 60
            delay_str = f" +{mins} хв"

        loc = f"{from_} → {to_}" if from_ and to_ else from_ or to_
        text = f"{emoji} <b>{label}</b>{road_str}{delay_str}\n    <i>{loc}</i>"
        if desc and desc.lower() not in label.lower():
            text += f"\n    {desc}"

        if cat in (1, 2, 3, 4):      accidents.append(text)
        elif cat == 8:                closures.append(text)
        elif cat in (6, 7):           slowdowns.append(text)
        elif cat in (5, 9):           works.append(text)
        else:                         other.append(text)

    if accidents:
        lines.append("🚗 <b>Аварії</b>")
        lines.extend(accidents)
        lines.append("")

    if closures:
        lines.append("🚧 <b>Перекрито</b>")
        lines.extend(closures)
        lines.append("")

    if slowdowns:
        lines.append("🐢 <b>Затори</b>")
        lines.extend(slowdowns)
        lines.append("")

    if works:
        lines.append("🔧 <b>Ремонтні роботи</b>")
        lines.extend(works)
        lines.append("")

    if other:
        lines.append("⚠️ <b>Інше</b>")
        lines.extend(other)

    total = len(incidents)
    lines.append(f"\n📍 Всього інцидентів: <b>{total}</b>")

    return "\n".join(lines)


if __name__ == "__main__":
    print(format_traffic_report())
