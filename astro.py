#!/usr/bin/env python3
"""
Астрологічний щоденний звіт.
Дані народження: 22.09.1989, 02:52, Кошіце (48.7163°N, 21.2611°E)
"""

import os, json, math, glob

# pyswisseph bundled sqlite — підвантажуємо її перед імпортом
_swe_libs = glob.glob("/usr/local/lib/python3*/dist-packages/pyswisseph.libs/libsqlite3*.so*")
if _swe_libs:
    import ctypes
    ctypes.CDLL(_swe_libs[0])

import swisseph as swe
from datetime import datetime, timezone, timedelta

# ─── Дані народження ──────────────────────────────────────────────────────────
BIRTH_DATE = (1989, 9, 22)
BIRTH_TIME = (2, 52, 0)
BIRTH_LAT  = 49.8383   # Львів 49N50'18
BIRTH_LON  = 24.0233   # Львів 24E01'24
BIRTH_TZ   = 4.0       # UTC+4 (Україна 1989, літній час — вересень)

# ─── Місто проживання (для будинків транзиту) ─────────────────────────────────
CURRENT_LAT = 48.7136   # Кошіце 48N42'50
CURRENT_LON = 21.2581   # Кошіце 21E15'29

# ─── Довідники ────────────────────────────────────────────────────────────────
PLANETS = [
    (swe.SUN,     "☀️ Сонце"),
    (swe.MOON,    "🌙 Місяць"),
    (swe.MERCURY, "☿ Меркурій"),
    (swe.VENUS,   "♀ Венера"),
    (swe.MARS,    "♂ Марс"),
    (swe.JUPITER, "♃ Юпітер"),
    (swe.SATURN,  "♄ Сатурн"),
    (swe.URANUS,  "⛢ Уран"),
    (swe.NEPTUNE, "♆ Нептун"),
    (swe.PLUTO,   "♇ Плутон"),
]

SIGNS_UA = [
    "Овен ♈", "Телець ♉", "Близнюки ♊", "Рак ♋",
    "Лев ♌", "Діва ♍", "Терези ♎", "Скорпіон ♏",
    "Стрілець ♐", "Козеріг ♑", "Водолій ♒", "Риби ♓",
]

HOUSES_UA = [
    "I (Особистість)", "II (Фінанси)", "III (Комунікація)",
    "IV (Дім/Родина)", "V (Творчість)", "VI (Здоров'я/Робота)",
    "VII (Партнерство)", "VIII (Трансформація)", "IX (Філософія)",
    "X (Кар'єра)", "XI (Друзі/Цілі)", "XII (Духовність)",
]

ASPECTS_UA = {
    0:   ("Кон'юнкція ☌", "🔴", 8),
    60:  ("Секстиль ⚹", "🟢", 6),
    90:  ("Квадрат □", "🔴", 8),
    120: ("Трин △", "🟢", 8),
    150: ("Квінконкс ⚻", "🟡", 3),
    180: ("Опозиція ☍", "🔴", 8),
}

MOON_PHASES_UA = [
    "🌑 Новий місяць", "🌒 Молодик (зростання)", "🌓 Перша чверть",
    "🌔 Прибуваючий гіббус", "🌕 Повний місяць", "🌖 Спадний гіббус",
    "🌗 Остання чверть", "🌘 Спадний серп",
]

PLANET_DESC = {
    "☀️ Сонце":    "ego, воля, особистість",
    "🌙 Місяць":   "емоції, інтуїція, звички",
    "☿ Меркурій": "розум, комунікація",
    "♀ Венера":   "любов, гроші, краса",
    "♂ Марс":     "дія, енергія, бажання",
    "♃ Юпітер":   "удача, зростання",
    "♄ Сатурн":   "дисципліна, обмеження",
    "⛢ Уран":     "зміни, несподіванки",
    "♆ Нептун":   "мрії, ілюзії",
    "♇ Плутон":   "трансформація, влада",
}

RETROGRADE_MEANING = {
    "☿ Меркурій": "⚠️ Меркурій ретро — обережно з контрактами, технікою, переговорами",
    "♀ Венера":   "⚠️ Венера ретро — переосмислення відносин і цінностей",
    "♂ Марс":     "⚠️ Марс ретро — енергія всередину, не починай нових проектів",
    "♃ Юпітер":   "ℹ️ Юпітер ретро — внутрішній ріст, переоцінка цілей",
    "♄ Сатурн":   "ℹ️ Сатурн ретро — ревізія структур і відповідальності",
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _to_jd(year, month, day, hour, minute, second, tz_offset=0):
    """Конвертує дату/час в Юліанський день (UTC)."""
    decimal_time = hour + minute / 60 + second / 3600 - tz_offset
    return swe.julday(year, month, day, decimal_time)

def _sign(lon):
    return SIGNS_UA[int(lon // 30)]

def _sign_deg(lon):
    deg = lon % 30
    return f"{int(deg)}°{int((deg % 1) * 60)}'"

def _house_num(lon, cusps):
    lon = lon % 360
    for i in range(11, -1, -1):
        cs = cusps[i] % 360
        ce = cusps[(i + 1) % 12] % 360
        if cs > ce:  # перехід через 0°
            if lon >= cs or lon < ce:
                return i + 1
        else:
            if cs <= lon < ce:
                return i + 1
    return 12

def _moon_phase(sun_lon, moon_lon):
    diff = (moon_lon - sun_lon) % 360
    idx = int(diff / 45)
    return MOON_PHASES_UA[idx], diff

def _angle_diff(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)

def _get_aspects(positions, orb_mult=1.0):
    """Знаходить аспекти між планетами."""
    aspects = []
    planet_names = [name for _, name in PLANETS]
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            diff = _angle_diff(positions[i], positions[j])
            for angle, (asp_name, emoji, orb) in ASPECTS_UA.items():
                if abs(diff - angle) <= orb * orb_mult:
                    exact = abs(diff - angle)
                    aspects.append((planet_names[i], planet_names[j], asp_name, emoji, exact))
    aspects.sort(key=lambda x: x[4])
    return aspects

def _transit_aspects(natal_positions, transit_positions, orb=3.0):
    """Транзитні аспекти до натальних планет."""
    natal_names   = [name for _, name in PLANETS]
    transit_names = [name for _, name in PLANETS]
    aspects = []
    for ti, t_lon in enumerate(transit_positions):
        for ni, n_lon in enumerate(natal_positions):
            diff = _angle_diff(t_lon, n_lon)
            for angle, (asp_name, emoji, base_orb) in ASPECTS_UA.items():
                if abs(diff - angle) <= orb:
                    exact = abs(diff - angle)
                    aspects.append((transit_names[ti], natal_names[ni], asp_name, emoji, exact))
    aspects.sort(key=lambda x: x[4])
    return aspects[:12]  # топ-12 найточніших

# ─── ОСНОВНА ФУНКЦІЯ ──────────────────────────────────────────────────────────

def get_astro_report():
    """Генерує повний астрологічний звіт на сьогодні."""
    swe.set_ephe_path(None)  # вбудована ефемерида

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc + timedelta(hours=2)
    today_str = now_local.strftime("%d.%m.%Y")
    weekday_ua = ["Понеділок","Вівторок","Середа","Четвер","П'ятниця","Субота","Неділя"][now_local.weekday()]

    # JD для транзитів (зараз)
    jd_now = _to_jd(now_utc.year, now_utc.month, now_utc.day,
                    now_utc.hour, now_utc.minute, now_utc.second, 0)

    # JD для натальної карти
    jd_natal = _to_jd(BIRTH_DATE[0], BIRTH_DATE[1], BIRTH_DATE[2],
                      BIRTH_TIME[0], BIRTH_TIME[1], BIRTH_TIME[2], BIRTH_TZ)

    # ── Транзитні планети ──
    transit_lons = []
    transit_retro = []
    for planet_id, _ in PLANETS:
        result, _ = swe.calc_ut(jd_now, planet_id)
        lon = result[0]
        speed = result[3]
        transit_lons.append(lon)
        transit_retro.append(speed < 0)

    # ── Натальні планети ──
    natal_lons = []
    for planet_id, _ in PLANETS:
        result, _ = swe.calc_ut(jd_natal, planet_id)
        natal_lons.append(result[0])

    # ── Натальні будинки (Placidus) — транзити показуємо через них, як у Sotis ──
    cusps_now, ascmc_now = swe.houses(jd_natal, BIRTH_LAT, BIRTH_LON, b"P")
    asc_now = ascmc_now[0]
    mc_now  = ascmc_now[1]

    # ── Фаза місяця ──
    moon_phase_name, moon_sun_diff = _moon_phase(transit_lons[0], transit_lons[1])

    # ── Місяць в знаку та прогноз ──
    moon_sign = _sign(transit_lons[1])
    moon_sign_tips = {
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

    # ── Формуємо звіт ──
    lines = [
        f"🔮 <b>АСТРОЛОГІЧНИЙ ЗВІТ</b>",
        f"📅 {weekday_ua}, {today_str}\n",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"\n{moon_phase_name}  ·  Місяць у <b>{moon_sign}</b>",
        f"<i>{moon_sign_tips.get(moon_sign, '')}</i>\n",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"\n🌍 <b>ПЛАНЕТИ СЬОГОДНІ</b>\n",
    ]

    for i, (planet_id, name) in enumerate(PLANETS):
        lon = transit_lons[i]
        sign = _sign(lon)
        deg = _sign_deg(lon)
        retro = "℞" if transit_retro[i] else ""
        house_n = _house_num(lon, cusps_now)
        house_str = f"  <i>дім {house_n}</i>"
        retro_str = f" <b>{retro}</b>" if retro else ""
        lines.append(f"{name}{retro_str}  →  {sign} {deg}{house_str}")

    # Ретроградні попередження
    retro_warnings = []
    for i, (_, name) in enumerate(PLANETS):
        if transit_retro[i] and name in RETROGRADE_MEANING:
            retro_warnings.append(RETROGRADE_MEANING[name])
    if retro_warnings:
        lines.append("")
        lines.extend(retro_warnings)

    # ── Транзитні аспекти між планетами ──
    t_aspects = _get_aspects(transit_lons)
    if t_aspects:
        lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"\n⚡ <b>АСПЕКТИ ДНЯ</b>\n")
        for p1, p2, asp, emoji, exact in t_aspects[:8]:
            lines.append(f"{emoji} {p1} {asp} {p2}  <i>(орб {exact:.1f}°)</i>")

    # ── Транзити до натальної карти ──
    natal_aspects = _transit_aspects(natal_lons, transit_lons)
    if natal_aspects:
        lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"\n🎯 <b>ТРАНЗИТИ ДО НАТАЛЬНИХ ПЛАНЕТ</b>")
        lines.append(f"<i>(як небо впливає особисто на тебе)</i>\n")
        for t_planet, n_planet, asp, emoji, exact in natal_aspects[:8]:
            lines.append(f"{emoji} Транзитний {t_planet} {asp} натальний {n_planet}  <i>({exact:.1f}°)</i>")

    # ── Прогноз на найближчі 7 днів (Місяць по знаках) ──
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"\n📆 <b>МІСЯЦЬ НА ТИЖДЕНЬ</b>\n")
    for delta in range(7):
        future = now_utc + timedelta(days=delta)
        jd_f = _to_jd(future.year, future.month, future.day, 12, 0, 0, 0)
        result, _ = swe.calc_ut(jd_f, swe.MOON)
        m_sign = _sign(result[0])
        day_name = ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"][(now_local + timedelta(days=delta)).weekday()]
        date_str = (now_local + timedelta(days=delta)).strftime("%d.%m")
        tip = moon_sign_tips.get(m_sign, "")[:40]
        lines.append(f"  {day_name} {date_str}  {m_sign}  <i>{tip}</i>")

    # ── Сонце в знаку (місячний прогноз) ──
    sun_sign = _sign(transit_lons[0])
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"\n☀️ <b>СОНЦЕ В {sun_sign.upper()}</b>")
    sun_tips = {
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
    lines.append(f"<i>{sun_tips.get(sun_sign, '')}</i>")

    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append("🌟 <i>Гарного дня!</i>")

    return "\n".join(lines)


if __name__ == "__main__":
    print(get_astro_report())
