"""
astro_houses.py — ТРАНЗИТИ ПО ДОМАХ + АСПЕКТИ З ДОМАМИ (глибокий астро-опис).

Запит Олега (28.08): «більше астро опису транзитів планет у домах, аспектів
планет в домах опис також, більше».

ЩО БУЛО: astro.py показував транзитну планету → знак і дім одним рядком
(«♃ Юпітер → Лев 8°42' дім 1»), а аспекти — без домів узагалі. Тобто дім був
цифрою без сенсу, а аспект висів у повітрі: незрозуміло, ЯКІ сфери життя
Олега він зчіпляє.

ЩО РОБИТЬ ЦЕЙ МОДУЛЬ (усе рахується по ефемеридах, нічого не вигадується):
  • для кожної транзитної планети: знак, градус, ℞, НАТАЛЬНИЙ ДІМ + що цей дім
    означає + що конкретно означає ця планета в цьому домі;
  • для кожного аспекту транзит→натал: дім транзитної планети І дім натальної
    (аспект завжди зчіпляє ДВІ сфери життя — це найцінніше);
  • сходиться чи розходиться (applying / separating) — рахується порівнянням
    орбу зараз і через 24 год, плюс дата точного аспекту для тих, що сходяться;
  • швидкість і напрямок руху (℞ / пряме), дні до виходу з орбу.

API:
    houses_facts()          -> str    # блок фактів для AI-промпту
    houses_report()         -> str    # /доми — детальний HTML-звіт Олегу
    transits_in_houses()    -> list   # сирі дані по планетах
    aspects_with_houses()   -> list   # сирі дані по аспектах
"""

from datetime import datetime, timedelta, timezone

TAG = "astro_houses"


def _log(m):
    print("[" + TAG + "] " + str(m), flush=True)


# ─── ЗНАЧЕННЯ ДОМІВ ──────────────────────────────────────────────────────────

HOUSE_MEANING = {
    1: ("I дім — Я, тіло, зовнішність, новий старт",
        "як тебе бачать, фізична форма, особиста ініціатива, перший крок"),
    2: ("II дім — гроші, ресурси, самооцінка",
        "заробіток власними руками, речі, відчуття «я вартий»"),
    3: ("III дім — інформація, навчання, короткі поїздки, брати/сестри",
        "листування, домовленості, дорога на роботу, швидке навчання"),
    4: ("IV дім — дім, сім'я, коріння, приватність",
        "квартира, батьки, тил, відчуття захищеності, відпочинок"),
    5: ("V дім — творчість, задоволення, ризик, діти, флірт",
        "спорт заради кайфу, ставки й спекуляції, романтика, самовираження"),
    6: ("VI дім — робота, рутина, здоров'я, дисципліна",
        "зміни на заводі, режим, харчування, тіло як механізм, лікарі"),
    7: ("VII дім — партнерство, стосунки, контракти, інші люди",
        "стосунки один-на-один, угоди, відкриті конфлікти, дзеркало"),
    8: ("VIII дім — чужі гроші, криза, трансформація, глибина",
        "кредити, інвестиції з ризиком, чужі ресурси, кардинальні зміни"),
    9: ("IX дім — сенси, далекі поїздки, вища освіта, світогляд",
        "закордон, велика картина, філософія, чому взагалі все це"),
    10: ("X дім — кар'єра, статус, репутація, мета",
         "робота як позиція в суспільстві, начальство, публічний результат"),
    11: ("XI дім — друзі, спільноти, плани на майбутнє, удача через людей",
         "нетворк, однодумці, довгі цілі, неочікувана допомога"),
    12: ("XII дім — підсвідоме, втома, самотність, завершення",
         "накопичена втома, сон, тихі процеси, те, що визріває невидимо"),
}

# коротко: що дає САМА планета, коли транзитом входить у дім
PLANET_IN_HOUSE = {
    "Сонце": "освітлює цю сферу — вона стає головною темою на ~30 днів",
    "Місяць": "робить сферу емоційною на 2-3 дні — реагуєш чутливіше",
    "Меркурій": "багато інформації, розмов і паперів у цій сфері",
    "Венера": "легкість, гроші й приємне в цій сфері — добре домовлятись",
    "Марс": "енергія й напір, але й конфлікт: тут треба ДІЯТИ, а не тягнути",
    "Юпітер": "розширення й можливості на ~рік — тут зараз найбільший ресурс",
    "Сатурн": "дисципліна, обмеження й перевірка на міцність — 2-3 роки",
    "Уран": "раптові зміни й зриви шаблону — стабільності тут не буде",
    "Нептун": "туман, ідеалізація, легко обманутись — не підписуй наосліп",
    "Плутон": "глибока перебудова сфери, назад дороги не буде",
}

ASPECT_TONE = {
    "Кон'юнкція": ("🔴", "злиття: тема вмикається на повну, нейтральних варіантів немає"),
    "Секстиль": ("🟢", "вікно можливості: працює, тільки якщо зробиш крок сам"),
    "Квадрат": ("🔴", "напруга й тертя: щось доведеться змінити, а не перетерпіти"),
    "Трин": ("🟢", "легкий потік: виходить майже без опору, гріх не використати"),
    "Квінконкс": ("🟡", "незручність: сфери не стикуються, потрібна адаптація"),
    "Опозиція": ("🔴", "полюси тягнуть у різні боки: потрібен баланс, не перемога"),
}


def _house_line(n):
    t, d = HOUSE_MEANING.get(n, ("дім " + str(n), ""))
    return t + " (" + d + ")"


# ─── РОЗРАХУНОК ──────────────────────────────────────────────────────────────

def _subjects(when=None):
    """(natal, transit, cusps) або (None, None, None) якщо kerykeion недоступний."""
    import astro as A
    if not A._KERYKEION_OK:
        return (None, None, None)
    from kerykeion import AstrologicalSubject
    now = when or datetime.now(timezone.utc)
    natal = AstrologicalSubject(
        "natal", A.BIRTH_YEAR, A.BIRTH_MONTH, A.BIRTH_DAY, A.BIRTH_HOUR,
        A.BIRTH_MIN, lat=A.BIRTH_LAT, lng=A.BIRTH_LON, tz_str=A.BIRTH_TZ,
        zodiac_type="Tropic", houses_system_identifier="P", online=False)
    transit = AstrologicalSubject(
        "transit", now.year, now.month, now.day, now.hour, now.minute,
        lat=A.CURRENT_LAT, lng=A.CURRENT_LON, tz_str="UTC",
        zodiac_type="Tropic", houses_system_identifier="P", online=False)
    cusps = [
        natal.first_house.abs_pos, natal.second_house.abs_pos,
        natal.third_house.abs_pos, natal.fourth_house.abs_pos,
        natal.fifth_house.abs_pos, natal.sixth_house.abs_pos,
        natal.seventh_house.abs_pos, natal.eighth_house.abs_pos,
        natal.ninth_house.abs_pos, natal.tenth_house.abs_pos,
        natal.eleventh_house.abs_pos, natal.twelfth_house.abs_pos,
    ]
    return (natal, transit, cusps)


def _clean(name_ua):
    """«♃ Юпітер» → «Юпітер»."""
    return "".join(ch for ch in str(name_ua) if ch.isalpha() or ch in "' ").strip()


def transits_in_houses():
    """Транзитні планети → натальні доми, з описом. [] якщо ефемериди недоступні."""
    import astro as A
    natal, transit, cusps = _subjects()
    if not natal:
        return []
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    _, transit2, _ = _subjects(tomorrow)
    out = []
    for key, name_ua in A.PLANETS_LIST:
        tp = getattr(transit, key, None)
        if not tp:
            continue
        lon = tp.abs_pos % 360
        house = A._get_natal_house(lon, cusps)
        nxt = getattr(transit2, key, None)
        speed = None
        if nxt:
            d = (nxt.abs_pos - tp.abs_pos + 540) % 360 - 180
            speed = round(d, 3)
        # скільки днів до виходу з дому (по швидкості)
        days_left = None
        try:
            idx = house - 1
            end = cusps[(idx + 1) % 12] % 360
            gap = (end - lon) % 360
            if speed and speed > 0.001:
                days_left = int(gap / speed)
        except Exception:
            pass
        clean = _clean(name_ua)
        out.append({
            "planet": name_ua,
            "clean": clean,
            "sign": A._sign_ua(tp.sign),
            "deg": A._deg_str(tp.position if hasattr(tp, "position") else lon % 30),
            "retro": bool(getattr(tp, "retrograde", False)),
            "house": house,
            "house_text": _house_line(house),
            "effect": PLANET_IN_HOUSE.get(clean, ""),
            "speed": speed,
            "days_left": days_left,
        })
    return out


def aspects_with_houses(max_items=14):
    """Аспекти транзит→натал з домами ОБОХ планет, орбом і напрямком."""
    import astro as A
    natal, transit, cusps = _subjects()
    if not natal:
        return []
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    _, transit2, _ = _subjects(tomorrow)

    natal_pts = []
    for key, name_ua in A.PLANETS_LIST:
        np = getattr(natal, key, None)
        if np:
            natal_pts.append((name_ua, np.abs_pos % 360))
    try:
        natal_pts.append(("⬆️ Асцендент", natal.first_house.abs_pos % 360))
        natal_pts.append(("🔝 MC", natal.tenth_house.abs_pos % 360))
    except Exception:
        pass

    found = []
    for key, t_name in A.PLANETS_LIST:
        tp = getattr(transit, key, None)
        if not tp:
            continue
        t_lon = tp.abs_pos % 360
        tp2 = getattr(transit2, key, None)
        t_lon2 = (tp2.abs_pos % 360) if tp2 else t_lon
        t_house = A._get_natal_house(t_lon, cusps)
        for n_name, n_lon in natal_pts:
            diff = A._angle_diff(t_lon, n_lon)
            diff2 = A._angle_diff(t_lon2, n_lon)
            for angle, (asp_name, emoji, orb) in A.ASPECTS_UA.items():
                cur = abs(diff - angle)
                if cur > orb:
                    continue
                nxt = abs(diff2 - angle)
                applying = nxt < cur
                per_day = abs(cur - nxt)
                days_exact = None
                if applying and per_day > 0.001:
                    days_exact = int(cur / per_day)
                n_house = A._get_natal_house(n_lon, cusps)
                base = asp_name.split()[0]
                tone_emo, tone_txt = ASPECT_TONE.get(base, (emoji, ""))
                found.append({
                    "t": t_name, "n": n_name, "asp": asp_name,
                    "emoji": tone_emo, "tone": tone_txt,
                    "orb": round(cur, 2),
                    "applying": applying,
                    "days_exact": days_exact,
                    "t_house": t_house, "n_house": n_house,
                    "t_house_text": _house_line(t_house),
                    "n_house_text": _house_line(n_house),
                    "retro": bool(getattr(tp, "retrograde", False)),
                })
                break
    found.sort(key=lambda x: (x["orb"], 0 if x["applying"] else 1))
    return found[:max_items]


# ─── БЛОК ДЛЯ AI ─────────────────────────────────────────────────────────────

def houses_facts(max_aspects=12):
    """Факти для промпту: доми + аспекти з домами. '' якщо ефемерид немає."""
    try:
        pl = transits_in_houses()
        asp = aspects_with_houses(max_aspects)
    except Exception as e:
        _log("houses_facts error: " + str(e))
        return ""
    if not pl and not asp:
        return ""
    rows = ["ТРАНЗИТНІ ПЛАНЕТИ ПО НАТАЛЬНИХ ДОМАХ ОЛЕГА (обчислено по ефемеридах):"]
    for p in pl:
        r = " ℞ретро" if p["retro"] else ""
        dl = ""
        if p["days_left"] is not None and p["days_left"] < 400:
            dl = ", у цьому домі ще ~" + str(p["days_left"]) + " дн."
        rows.append("• " + p["clean"] + r + " у " + str(p["sign"]) + " → "
                    + p["house_text"] + dl
                    + (". Дія: " + p["effect"] if p["effect"] else ""))
    if asp:
        rows.append("")
        rows.append("АСПЕКТИ ТРАНЗИТ→НАТАЛ З ДОМАМИ ОБОХ ТОЧОК "
                    "(аспект зчіпляє ДВІ сфери життя — це головне):")
        for a in asp:
            mv = "сходиться" if a["applying"] else "розходиться"
            when = ""
            if a["days_exact"] is not None and a["days_exact"] <= 60:
                when = ", точний через ~" + str(a["days_exact"]) + " дн."
            rows.append(
                "• транзитний " + _clean(a["t"]) + " (зараз у " + a["t_house_text"]
                + ") " + a["asp"] + " натальний " + _clean(a["n"])
                + " (у " + a["n_house_text"] + "), орб " + str(a["orb"])
                + "°, " + mv + when + ". Характер: " + a["tone"])
    rows.append("")
    rows.append("ЯК ЦЕ ВИКОРИСТАТИ В ОПИСІ (обов'язково): для кожного значного "
                "транзиту скажи, ЯКА СФЕРА життя Олега активна (по дому) і що "
                "конкретно робити; для аспектів — назви ОБИДВІ сфери й опиши, як "
                "вони зчіплюються між собою. Орб і «сходиться/розходиться» "
                "показують, наростає тема чи вже слабне — не плутай. "
                "Не вигадуй домів і дат, яких немає в цих даних.")
    return "\n".join(rows)


# ─── /доми ───────────────────────────────────────────────────────────────────

def houses_report():
    try:
        pl = transits_in_houses()
        asp = aspects_with_houses(14)
    except Exception as e:
        return "⚠️ Астро-доми недоступні: " + str(e)[:200]
    if not pl:
        return ("⚠️ Ефемериди недоступні (kerykeion не завантажився) — "
                "доми порахувати не можу. Не вигадую.")

    out = ["🏠 <b>ТРАНЗИТИ ПО ДОМАХ</b>", ""]
    by_house = {}
    for p in pl:
        by_house.setdefault(p["house"], []).append(p)
    for h in sorted(by_house):
        title, desc = HOUSE_MEANING.get(h, ("дім " + str(h), ""))
        out.append("<b>" + title + "</b>")
        out.append("<i>" + desc + "</i>")
        for p in by_house[h]:
            r = " ℞" if p["retro"] else ""
            dl = ""
            if p["days_left"] is not None and p["days_left"] < 400:
                dl = " · ще ~" + str(p["days_left"]) + " дн."
            out.append("  " + p["planet"] + r + " — " + str(p["sign"])
                       + " " + str(p["deg"]) + dl)
            if p["effect"]:
                out.append("  → " + p["effect"])
        out.append("")

    if asp:
        out.append("🔗 <b>АСПЕКТИ З ДОМАМИ</b> (сфера ↔ сфера)")
        out.append("")
        for a in asp:
            mv = "▲ сходиться" if a["applying"] else "▼ розходиться"
            when = ""
            if a["days_exact"] is not None and a["days_exact"] <= 60:
                when = " · точний через ~" + str(a["days_exact"]) + " дн."
            out.append(a["emoji"] + " <b>" + _clean(a["t"]) + " " + a["asp"]
                       + " натальний " + _clean(a["n"]) + "</b> ("
                       + str(a["orb"]) + "° · " + mv + when + ")")
            out.append("   " + str(a["t_house"]) + "-й дім ↔ "
                       + str(a["n_house"]) + "-й дім: "
                       + a["t_house_text"].split(" — ")[-1].split(" (")[0]
                       + " ↔ "
                       + a["n_house_text"].split(" — ")[-1].split(" (")[0])
            out.append("   <i>" + a["tone"] + "</i>")
            out.append("")
    out.append("Усе обчислено по ефемеридах на цю хвилину. "
               "Розгорнутий AI-розбір — у команді /астро.")
    return "\n".join(out)
