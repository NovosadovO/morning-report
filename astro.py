#!/usr/bin/env python3
"""
Астрологічний щоденний звіт — використовує kerykeion (чистий Python, без C залежностей).
Дані народження: 22.09.1989, 02:52, UTC+4, Львів (49N50'18, 24E01'24)
Транзитні будинки: натальні (Placidus, Львів) — як у Sotis.
"""

import os, warnings
warnings.filterwarnings("ignore")

from datetime import datetime, timezone, timedelta
try:
    from kerykeion import AstrologicalSubject
    _KERYKEION_OK = True
except ImportError:
    _KERYKEION_OK = False

# ─── Дані народження ──────────────────────────────────────────────────────────
BIRTH_YEAR, BIRTH_MONTH, BIRTH_DAY = 1989, 9, 22
BIRTH_HOUR, BIRTH_MIN = 2, 52
BIRTH_LAT, BIRTH_LON = 49.8383, 24.0233   # Львів
BIRTH_TZ = "Europe/Kiev"

# ─── Місто проживання ─────────────────────────────────────────────────────────
CURRENT_LAT, CURRENT_LON = 48.7136, 21.2581  # Кошіце
CURRENT_TZ = "Europe/Bratislava"

# ─── Довідники ────────────────────────────────────────────────────────────────
SIGNS_UA = {
    "Ari": "Овен ♈", "Tau": "Телець ♉", "Gem": "Близнюки ♊", "Can": "Рак ♋",
    "Leo": "Лев ♌",  "Vir": "Діва ♍",  "Lib": "Терези ♎",  "Sco": "Скорпіон ♏",
    "Sag": "Стрілець ♐", "Cap": "Козеріг ♑", "Aqu": "Водолій ♒", "Pis": "Риби ♓",
}

PLANETS_LIST = [
    ("sun",     "☀️ Сонце"),
    ("moon",    "🌙 Місяць"),
    ("mercury", "☿ Меркурій"),
    ("venus",   "♀ Венера"),
    ("mars",    "♂ Марс"),
    ("jupiter", "♃ Юпітер"),
    ("saturn",  "♄ Сатурн"),
    ("uranus",  "⛢ Уран"),
    ("neptune", "♆ Нептун"),
    ("pluto",   "♇ Плутон"),
]

MOON_PHASES_UA = [
    "🌑 Новий місяць", "🌒 Молодик (зростання)", "🌓 Перша чверть",
    "🌔 Прибуваючий гіббус", "🌕 Повний місяць", "🌖 Спадний гіббус",
    "🌗 Остання чверть", "🌘 Спадний серп",
]

MOON_SIGN_TIPS = {
    "Овен ♈":     "⚡ Емоції на піку — добре для старту нових справ",
    "Телець ♉":   "🌿 Час для комфорту, фінансів, задоволень",
    "Близнюки ♊": "💬 Активна комунікація, навчання, ідеї",
    "Рак ♋":      "🏠 Фокус на домі, родині, емоційній безпеці",
    "Лев ♌":      "🎭 Творчість, впевненість, час сяяти",
    "Діва ♍":     "📋 Деталі, здоров'я, продуктивність — ідеально для роботи",
    "Терези ♎":   "⚖️ Баланс, відносини, переговори",
    "Скорпіон ♏": "🔍 Глибина, трансформація, інтуїція загострена",
    "Стрілець ♐": "🏹 Оптимізм, подорожі, філософія",
    "Козеріг ♑":  "🏔 Серйозність, амбіції, структура",
    "Водолій ♒":  "🔭 Інновації, друзі, нестандартні рішення",
    "Риби ♓":     "🌊 Мрії, духовність, творчість — обережно з реальністю",
}

RETROGRADE_MEANING = {
    "☿ Меркурій": "⚠️ Меркурій ретро — обережно з контрактами, технікою, переговорами",
    "♀ Венера":   "⚠️ Венера ретро — переосмислення відносин і цінностей",
    "♂ Марс":     "⚠️ Марс ретро — енергія всередину, не починай нових проектів",
    "♃ Юпітер":   "ℹ️ Юпітер ретро — внутрішній ріст, переоцінка цілей",
    "♄ Сатурн":   "ℹ️ Сатурн ретро — ревізія структур і відповідальності",
}

ASPECTS_UA = {
    0:   ("Кон'юнкція ☌", "🔴", 8),
    60:  ("Секстиль ⚹",   "🟢", 6),
    90:  ("Квадрат □",    "🔴", 8),
    120: ("Трин △",       "🟢", 8),
    150: ("Квінконкс ⚻",  "🟡", 3),
    180: ("Опозиція ☍",   "🔴", 8),
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _get_planet(subject, key):
    return getattr(subject, key, None)

def _sign_ua(sign_key):
    return SIGNS_UA.get(sign_key, sign_key)

def _deg_str(pos):
    d = int(pos)
    m = int((pos % 1) * 60)
    return f"{d}°{m}'"

def _get_natal_house(lon, cusps):
    lon = lon % 360
    for i in range(11, -1, -1):
        cs = cusps[i] % 360
        ce = cusps[(i + 1) % 12] % 360
        if cs > ce:
            if lon >= cs or lon < ce:
                return i + 1
        else:
            if cs <= lon < ce:
                return i + 1
    return 12

def _angle_diff(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)

def _moon_phase(sun_lon, moon_lon):
    diff = (moon_lon - sun_lon) % 360
    idx = int(diff / 45)
    return MOON_PHASES_UA[idx], diff

def _get_aspects(lons, names):
    aspects = []
    for i in range(len(lons)):
        for j in range(i + 1, len(lons)):
            diff = _angle_diff(lons[i], lons[j])
            for angle, (asp_name, emoji, orb) in ASPECTS_UA.items():
                if abs(diff - angle) <= orb:
                    aspects.append((names[i], names[j], asp_name, emoji, abs(diff - angle)))
    aspects.sort(key=lambda x: x[4])
    return aspects

def _transit_aspects(natal_lons, transit_lons, natal_names, transit_names, orb=3.0):
    aspects = []
    for ti, t_lon in enumerate(transit_lons):
        for ni, n_lon in enumerate(natal_lons):
            diff = _angle_diff(t_lon, n_lon)
            for angle, (asp_name, emoji, _) in ASPECTS_UA.items():
                if abs(diff - angle) <= orb:
                    aspects.append((transit_names[ti], natal_names[ni], asp_name, emoji, abs(diff - angle)))
    aspects.sort(key=lambda x: x[4])
    return aspects[:12]

# ─── ОСНОВНА ФУНКЦІЯ ──────────────────────────────────────────────────────────

def get_astro_report():
    if not _KERYKEION_OK:
        return "♈ <b>Астро</b>\n⚠️ kerykeion не встановлений"
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc + timedelta(hours=2)
    today_str = now_local.strftime("%d.%m.%Y")
    weekday_ua = ["Понеділок","Вівторок","Середа","Четвер","П'ятниця","Субота","Неділя"][now_local.weekday()]

    # Натальна карта
    natal = AstrologicalSubject(
        "natal",
        BIRTH_YEAR, BIRTH_MONTH, BIRTH_DAY, BIRTH_HOUR, BIRTH_MIN,
        lat=BIRTH_LAT, lng=BIRTH_LON,
        tz_str=BIRTH_TZ,
        zodiac_type="Tropic",
        houses_system_identifier="P",
        online=False,
    )

    # Транзит — поточний момент
    transit = AstrologicalSubject(
        "transit",
        now_utc.year, now_utc.month, now_utc.day,
        now_utc.hour, now_utc.minute,
        lat=CURRENT_LAT, lng=CURRENT_LON,
        tz_str="UTC",
        zodiac_type="Tropic",
        houses_system_identifier="P",
        online=False,
    )

    # Натальні cusps
    natal_cusps = [
        natal.first_house.abs_pos,  natal.second_house.abs_pos,  natal.third_house.abs_pos,
        natal.fourth_house.abs_pos, natal.fifth_house.abs_pos,   natal.sixth_house.abs_pos,
        natal.seventh_house.abs_pos,natal.eighth_house.abs_pos,  natal.ninth_house.abs_pos,
        natal.tenth_house.abs_pos,  natal.eleventh_house.abs_pos, natal.twelfth_house.abs_pos,
    ]

    # Збираємо транзитні і натальні планети
    transit_data = []   # (name_ua, lon, pos, sign_ua, retro)
    natal_data   = []

    for key, name_ua in PLANETS_LIST:
        tp = _get_planet(transit, key)
        np = _get_planet(natal, key)
        if tp:
            transit_data.append((name_ua, tp.abs_pos, tp.position, _sign_ua(tp.sign), tp.retrograde))
        if np:
            natal_data.append((name_ua, np.abs_pos))

    # Фаза місяця
    sun_lon  = transit_data[0][1]
    moon_lon = transit_data[1][1]
    moon_phase_name, _ = _moon_phase(sun_lon, moon_lon)
    moon_sign_ua = transit_data[1][3]

    # ── Формуємо звіт ──
    lines = [
        f"🔮 <b>АСТРОЛОГІЧНИЙ ЗВІТ</b>",
        f"📅 {weekday_ua}, {today_str}\n",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"\n{moon_phase_name}  ·  Місяць у <b>{moon_sign_ua}</b>",
        f"<i>{MOON_SIGN_TIPS.get(moon_sign_ua, '')}</i>\n",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"\n🌍 <b>ПЛАНЕТИ СЬОГОДНІ</b>\n",
    ]

    transit_lons  = []
    transit_names = []
    retro_warnings = []

    for name_ua, lon, pos, sign_ua, retro in transit_data:
        house_n = _get_natal_house(lon, natal_cusps)
        retro_str = " <b>℞</b>" if retro else ""
        lines.append(f"{name_ua}{retro_str}  →  {sign_ua} {_deg_str(pos)}  <i>дім {house_n}</i>")
        transit_lons.append(lon)
        transit_names.append(name_ua)
        if retro and name_ua in RETROGRADE_MEANING:
            retro_warnings.append(RETROGRADE_MEANING[name_ua])

    if retro_warnings:
        lines.append("")
        lines.extend(retro_warnings)

    # Аспекти дня
    t_aspects = _get_aspects(transit_lons, transit_names)
    if t_aspects:
        lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"\n⚡ <b>АСПЕКТИ ДНЯ</b>\n")
        for p1, p2, asp, emoji, exact in t_aspects[:8]:
            lines.append(f"{emoji} {p1} {asp} {p2}  <i>(орб {exact:.1f}°)</i>")

    # Транзити до натальної карти
    natal_lons  = [lon for _, lon in natal_data]
    natal_names = [name for name, _ in natal_data]
    n_aspects = _transit_aspects(natal_lons, transit_lons, natal_names, transit_names)
    if n_aspects:
        lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"\n🎯 <b>ТРАНЗИТИ ДО НАТАЛЬНИХ ПЛАНЕТ</b>")
        lines.append(f"<i>(як небо впливає особисто на тебе)</i>\n")
        for t_planet, n_planet, asp, emoji, exact in n_aspects[:8]:
            lines.append(f"{emoji} Транзитний {t_planet} {asp} натальний {n_planet}  <i>({exact:.1f}°)</i>")

    # Місяць на тиждень
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"\n📆 <b>МІСЯЦЬ НА ТИЖДЕНЬ</b>\n")
    for delta in range(7):
        future_utc = now_utc + timedelta(days=delta)
        future_local = now_local + timedelta(days=delta)
        m = AstrologicalSubject(
            "m", future_utc.year, future_utc.month, future_utc.day, 12, 0,
            lat=CURRENT_LAT, lng=CURRENT_LON, tz_str="UTC",
            zodiac_type="Tropic", houses_system_identifier="P", online=False,
        )
        m_sign = _sign_ua(m.moon.sign)
        day_name = ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"][future_local.weekday()]
        date_str = future_local.strftime("%d.%m")
        tip = MOON_SIGN_TIPS.get(m_sign, "")[:40]
        lines.append(f"  {day_name} {date_str}  {m_sign}  <i>{tip}</i>")

    # Сонце в знаку
    sun_sign_ua = transit_data[0][3]
    SUN_TIPS = {
        "Телець ♉":   "Квітень–Травень: фінанси, стабільність, матеріальне",
        "Близнюки ♊": "Травень–Червень: комунікація, навчання, поїздки",
        "Рак ♋":      "Червень–Липень: родина, дім, емоції",
        "Лев ♌":      "Липень–Серпень: лідерство, творчість, визнання",
        "Діва ♍":     "Серпень–Вересень: здоров'я, деталі, служіння",
        "Терези ♎":   "Вересень–Жовтень: баланс, відносини, справедливість",
        "Скорпіон ♏": "Жовтень–Листопад: трансформація, таємниці, глибина",
        "Стрілець ♐": "Листопад–Грудень: оптимізм, подорожі, навчання",
        "Козеріг ♑":  "Грудень–Січень: кар'єра, відповідальність, терпіння",
        "Водолій ♒":  "Січень–Лютий: новаторство, команда, майбутнє",
        "Риби ♓":     "Лютий–Березень: духовність, творчість, завершення",
        "Овен ♈":     "Березень–Квітень: нові початки, ініціатива, сміливість",
    }
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"\n☀️ <b>СОНЦЕ В {sun_sign_ua.upper()}</b>")
    lines.append(f"<i>{SUN_TIPS.get(sun_sign_ua, '')}</i>")
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append("🌟 <i>Гарного дня!</i>")

    return "\n".join(lines)


if __name__ == "__main__":
    print(get_astro_report())
