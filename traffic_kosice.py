#!/usr/bin/env python3
"""
Трафік Кошіце через TomTom API — коротко і по суті.
"""

import os, json, urllib.request, urllib.parse

TOMTOM_KEY = os.environ.get("TOMTOM_API_KEY", "cx2m0M3xY0hgqlwUeroRTKg4txk9HRph")
BBOX = "21.20,48.65,21.30,48.75"

# Тільки серйозні категорії
SERIOUS = {
    1: "🚗 Аварія",
    2: "🚗 Аварія",
    3: "🚗 Аварія",
    4: "🚗 Аварія",
    8: "🚧 Перекрито",
}
MINOR = {
    5: "🔧 Ремонт",
    6: "🐢 Затор",
    7: "🐢 Затор",
    9: "🔧 Ремонт",
}


def get_incidents():
    fields = "{incidents{type,properties{iconCategory,magnitudeOfDelay,events{description},from,to,delay}}}"
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
            return json.loads(r.read()).get("incidents", [])
    except Exception as e:
        print(f"TomTom error: {e}")
        return []


def format_traffic_report():
    incidents = get_incidents()

    serious = []
    minor_count = 0

    for inc in incidents:
        props = inc.get("properties", {})
        cat   = props.get("iconCategory", 0)
        mag   = props.get("magnitudeOfDelay", 0)
        from_ = props.get("from", "")
        to_   = props.get("to", "")
        delay = props.get("delay")

        loc = from_.split("(")[0].strip() if from_ else ""

        if cat in SERIOUS:
            label = SERIOUS[cat]
            delay_str = f" (+{delay//60} хв)" if delay and delay > 60 else ""
            serious.append(f"{label}{delay_str} — {loc}")
        elif cat in MINOR and mag >= 2:
            minor_count += 1

    # Загальна оцінка
    if not incidents:
        status = "🟢 Дороги вільні"
    elif serious:
        status = "🔴 Є проблеми"
    elif minor_count > 0:
        status = "🟡 Невеликі затримки"
    else:
        status = "🟢 Загалом вільно"

    lines = [f"🚦 <b>Трафік Кошіце</b> — {status}"]

    if serious:
        lines.append("")
        for s in serious[:4]:  # максимум 4 серйозних
            lines.append(f"  • {s}")

    if minor_count > 0 and not serious:
        lines.append(f"  • Дрібних інцидентів: {minor_count}")

    return "\n".join(lines)


if __name__ == "__main__":
    print(format_traffic_report())
