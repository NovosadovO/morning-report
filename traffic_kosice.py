#!/usr/bin/env python3
"""
Трафік Кошіце через TomTom API — коротко і по суті.

Раніше показувались просто перші 4 "серйозних" інциденти в тому порядку, в
якому TomTom їх повертає — а порядок стабільний, тож у звіті стабільно
опинялись ОДНІ Й ТІ САМІ довготривалі перекриття (ремонти, що тримаються
тижнями), і звіт виглядав "завжди той самий", навіть коли з'являлись нові
аварії/затори. Тепер порівнюємо з попереднім знімком (persist через
storage.py) і окремо показуємо НОВІ інциденти, а старі — одним підсумковим
рядком "без змін", щоб не займати місце й не створювати враження, що дані
не оновлюються.
"""

import os, json, time, urllib.request, urllib.parse

TOMTOM_KEY = os.environ.get("TOMTOM_API_KEY", "cx2m0M3xY0hgqlwUeroRTKg4txk9HRph")
BBOX = "21.20,48.65,21.30,48.75"

_STATE_FILE = "traffic_state.json"
# Базовий знімок оновлюємо не частіше ніж раз на N секунд — інакше кілька
# різних викликів (hourly-звіт, AI-контекст, проактивні сповіщення) в межах
# однієї й тієї самої години перезаписували б знімок один за одним, і кожен
# наступний виклик бачив би щойно з'явлені інциденти як "старі".
_BASELINE_TTL = 40 * 60

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


def _load_baseline():
    """Повертає (keys_set, is_stale). is_stale=True — час оновити знімок."""
    try:
        import storage
        state = storage.load(_STATE_FILE, default={}) or {}
        ts = state.get("ts", 0)
        keys = set(state.get("keys", []))
        is_stale = (time.time() - ts) > _BASELINE_TTL
        return keys, is_stale
    except Exception as e:
        print(f"traffic state load error: {e}")
        return set(), True


def _save_baseline(keys):
    try:
        import storage
        storage.save(_STATE_FILE, {"ts": time.time(), "keys": sorted(keys)})
    except Exception as e:
        print(f"traffic state save error: {e}")


def format_traffic_report():
    incidents = get_incidents()

    serious_new = []      # (label, loc) — з'явились відносно минулого разу
    serious_old = []      # (label, loc) — тримаються ще з минулого разу
    minor_count = 0

    baseline_keys, is_stale = _load_baseline()
    cur_keys = set()

    for inc in incidents:
        props = inc.get("properties", {})
        cat   = props.get("iconCategory", 0)
        mag   = props.get("magnitudeOfDelay", 0)
        from_ = props.get("from", "")
        to_   = props.get("to", "")
        delay = props.get("delay")

        loc = from_.split("(")[0].strip() if from_ else ""
        key = f"{cat}:{from_}:{to_}"

        is_serious = cat in SERIOUS
        is_jam     = cat in (6, 7)

        if is_serious or is_jam:
            label = SERIOUS.get(cat, "🐢 Затор")
            delay_str = f" (+{delay//60} хв)" if delay and delay > 60 else ""
            entry = f"{label}{delay_str} — {loc}"
            cur_keys.add(key)
            if key in baseline_keys:
                serious_old.append(entry)
            else:
                serious_new.append(entry)
        elif cat in MINOR and mag >= 2:
            minor_count += 1
            cur_keys.add(key)

    # Оновлюємо знімок-базу не частіше ніж раз на _BASELINE_TTL — щоб кілька
    # викликів протягом однієї години порівнювались з ОДНИМ і тим самим станом.
    if is_stale:
        _save_baseline(cur_keys)

    serious_all = serious_new + serious_old

    # Загальна оцінка
    if not incidents:
        status = "🟢 Дороги вільні"
    elif serious_all:
        status = "🔴 Є проблеми"
    elif minor_count > 0:
        status = "🟡 Невеликі затримки"
    else:
        status = "🟢 Загалом вільно"

    lines = [f"🚦 <b>Трафік Кошіце</b> — {status}"]

    if serious_new:
        lines.append("")
        lines.append("<b>🆕 Нові:</b>")
        for s in serious_new[:5]:
            lines.append(f"  • {s}")

    if serious_old:
        lines.append("")
        if serious_new:
            lines.append(f"<i>+ {len(serious_old)} довготривалих без змін</i>")
        else:
            # Немає нових — не повторюємо той самий перелік щоразу, а даємо
            # компактне зведення "без змін" з 1-2 прикладами.
            lines.append(f"<i>🚧 {len(serious_old)} довготривалих перекриттів — без змін від минулого разу:</i>")
            for s in serious_old[:2]:
                lines.append(f"  • {s}")
            if len(serious_old) > 2:
                lines.append(f"<i>+ ще {len(serious_old) - 2}</i>")

    if minor_count > 0 and not serious_all:
        lines.append(f"  • Дрібних інцидентів: {minor_count}")

    return "\n".join(lines)


if __name__ == "__main__":
    print(format_traffic_report())
