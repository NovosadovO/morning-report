#!/usr/bin/env python3
"""
Натальна карта + транзити — точна копія референсного стилю.
Знаки: за годинниковою (CW), ASC праворуч, Aries вгорі.
Натальні планети: зовні між R_TRANSIT і R_SIGN_IN, чорні, великі.
Транзитні планети: між R_NATAL і R_SIGN_IN (зовнє кільце), зеленувато-жовті крапки.
"""
import os, math, tempfile, warnings, subprocess, sys
warnings.filterwarnings("ignore")

for _pkg in ("numpy", "matplotlib"):
    try:
        __import__(_pkg)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", _pkg])

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from datetime import datetime, timezone, timedelta

try:
    from kerykeion import AstrologicalSubject
    _OK = True
except ImportError:
    _OK = False

# ─── Дані ─────────────────────────────────────────────────────────────────────
BIRTH_YEAR, BIRTH_MONTH, BIRTH_DAY = 1989, 9, 22
BIRTH_HOUR, BIRTH_MIN = 2, 52
BIRTH_LAT, BIRTH_LON = 49.8383, 24.0233
BIRTH_TZ = "Europe/Kiev"
CURRENT_LAT, CURRENT_LON = 48.7136, 21.2581

SIGN_SYMBOLS = ['♈','♉','♊','♋','♌','♍','♎','♏','♐','♑','♒','♓']

# Кольори знаків як на референсі (по стихіях, чергуються):
# Овен=червоний, Телець=помаранчевий, Близнюки=індіго, Рак=зелений (і повтор)
SIGN_COLORS = [
    '#E53935',  # Овен       — вогонь, червоний
    '#FF9800',  # Телець     — земля,  помаранчевий
    '#7986CB',  # Близнюки   — повітря, індіго
    '#43A047',  # Рак        — вода,   зелений
    '#E53935',  # Лев        — вогонь
    '#FF9800',  # Діва       — земля
    '#7986CB',  # Терези     — повітря
    '#43A047',  # Скорпіон   — вода
    '#E53935',  # Стрілець   — вогонь
    '#FF9800',  # Козеріг    — земля
    '#7986CB',  # Водолій    — повітря
    '#43A047',  # Риби       — вода
]

PLANET_SYMBOLS = {
    'sun':'☉','moon':'☽','mercury':'☿','venus':'♀','mars':'♂',
    'jupiter':'♃','saturn':'♄','uranus':'⛢','neptune':'♆','pluto':'♇',
}

PLANETS_LIST = ['sun','moon','mercury','venus','mars','jupiter','saturn','uranus','neptune','pluto']

ASP_ANGLES = {0:'conjunction',60:'sextile',90:'square',120:'trine',150:'quincunx',180:'opposition'}
ASP_ORB    = {0:8, 60:6, 90:7, 120:8, 150:3, 180:8}
ASP_SYMBOLS = {
    'conjunction':'☌','trine':'△','sextile':'✱',
    'square':'□','opposition':'☍','quincunx':'⚻'
}


def _lon_to_rad_cw(lon, asc_lon=0.0):
    """
    Конвертує еклиптичну довготу в кут для малювання.
    Референс: ASC праворуч (0 рад), знаки йдуть за годинниковою (CW).
    angle = - (lon - asc_lon) в радіанах  →  CW від ASC
    """
    return math.radians(-(lon - asc_lon))


def _get_aspects(lons_a, names_a, lons_b, names_b, orb=8.0, cross=False):
    result = []
    for i,(la,na) in enumerate(zip(lons_a, names_a)):
        for j,(lb,nb) in enumerate(zip(lons_b, names_b)):
            if not cross and i >= j: continue
            diff = abs(la - lb) % 360
            if diff > 180: diff = 360 - diff
            for deg, name in ASP_ANGLES.items():
                o = min(orb, ASP_ORB[deg])
                if abs(diff - deg) <= o:
                    result.append((na, nb, name, abs(diff - deg)))
    return result


def _spread_planets(lons, min_gap=9.0):
    indexed = sorted(enumerate(lons), key=lambda x: x[1])
    display = list(lons)
    for _ in range(300):
        changed = False
        for k in range(len(indexed)):
            ia, _ = indexed[k]
            ib, _ = indexed[(k+1) % len(indexed)]
            diff = (display[ib] - display[ia]) % 360
            if 0 < diff < min_gap:
                push = (min_gap - diff) / 2.0 + 0.3
                display[ia] = (display[ia] - push) % 360
                display[ib] = (display[ib] + push) % 360
                changed = True
        if not changed:
            break
    return display


def generate_natal_chart(output_path=None):
    if not _OK:
        return None

    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        output_path = tmp.name
        tmp.close()

    now_utc   = datetime.now(timezone.utc)
    now_local = now_utc + timedelta(hours=2)

    natal = AstrologicalSubject(
        "natal", BIRTH_YEAR, BIRTH_MONTH, BIRTH_DAY, BIRTH_HOUR, BIRTH_MIN,
        lat=BIRTH_LAT, lng=BIRTH_LON, tz_str=BIRTH_TZ,
        zodiac_type="Tropic", houses_system_identifier="P", online=False,
    )
    transit = AstrologicalSubject(
        "transit", now_utc.year, now_utc.month, now_utc.day, now_utc.hour, now_utc.minute,
        lat=CURRENT_LAT, lng=CURRENT_LON, tz_str="UTC",
        zodiac_type="Tropic", houses_system_identifier="P", online=False,
    )

    house_keys = [
        'first_house','second_house','third_house','fourth_house',
        'fifth_house','sixth_house','seventh_house','eighth_house',
        'ninth_house','tenth_house','eleventh_house','twelfth_house'
    ]
    cusps = [getattr(natal, k).abs_pos for k in house_keys]
    asc_lon = cusps[0]  # ASC = 1-й будинок

    natal_lons, natal_names = [], []
    for key in PLANETS_LIST:
        try:
            natal_lons.append(getattr(natal, key).abs_pos)
            natal_names.append(key)
        except: pass

    transit_lons, transit_names = [], []
    for key in PLANETS_LIST:
        try:
            transit_lons.append(getattr(transit, key).abs_pos)
            transit_names.append(key)
        except: pass

    natal_aspects   = _get_aspects(natal_lons, natal_names, natal_lons, natal_names, orb=8)
    transit_aspects = _get_aspects(transit_lons, transit_names, natal_lons, natal_names, orb=5, cross=True)

    natal_display   = _spread_planets(natal_lons,   min_gap=10.0)
    transit_display = _spread_planets(transit_lons, min_gap=10.0)

    # ─── Полотно ──────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 16), facecolor='white')
    ax  = fig.add_axes([0.03, 0.03, 0.94, 0.94], facecolor='white')
    ax.set_xlim(-1.62, 1.62)
    ax.set_ylim(-1.68, 1.68)
    ax.set_aspect('equal')
    ax.axis('off')

    # ─── Радіуси ──────────────────────────────────────────────────────────────
    R_SIGN_OUT   = 1.45   # зовнішній край кольорового кільця знаків
    R_SIGN_IN    = 1.14   # внутрішній край кільця знаків
    R_TRANSIT_P  = 1.06   # кільце транзитних планет (між знаками і натальними)
    R_NATAL_P    = 0.94   # кільце натальних планет
    R_HOUSE_OUT  = R_SIGN_IN
    R_HOUSE_IN   = 0.38   # лінії будинків до центру
    R_HOUSE_LBL  = 0.82   # номери будинків (між inner і natal)
    R_ASP        = 0.70   # аспектні лінії
    R_CENTER     = 0.30

    def add_circle(r, color='#555555', lw=1.0, ls='-', zorder=2):
        c = plt.Circle((0,0), r, color=color, fill=False, linewidth=lw, linestyle=ls, zorder=zorder)
        ax.add_patch(c)

    def xy(r, angle_rad):
        return r*math.cos(angle_rad), r*math.sin(angle_rad)

    # ─── Кольорові сектори знаків ─────────────────────────────────────────────
    for i in range(12):
        lon_start = i * 30
        lon_mid   = lon_start + 15

        # Кути: CW від ASC → theta в matplotlib = звичайний CCW кут
        # matplotlib wedge: theta1 < theta2, CCW
        a_end   = math.degrees(-( lon_start        - asc_lon))
        a_start = math.degrees(-((lon_start + 30)  - asc_lon))
        # Нормуємо
        col = SIGN_COLORS[i]
        wedge = mpatches.Wedge(
            (0,0), R_SIGN_OUT, a_start, a_end,
            width=(R_SIGN_OUT - R_SIGN_IN),
            color=col, alpha=1.0, zorder=3
        )
        ax.add_patch(wedge)

        # Символ знаку (великий, білий, по центру сектора)
        a_mid = _lon_to_rad_cw(lon_mid, asc_lon)
        r_mid = (R_SIGN_IN + R_SIGN_OUT) / 2.0
        ax.text(r_mid*math.cos(a_mid), r_mid*math.sin(a_mid),
                SIGN_SYMBOLS[i], ha='center', va='center',
                fontsize=28, color='white', fontweight='bold',
                zorder=6, fontfamily='DejaVu Sans')

        # Розподільна лінія між знаками
        a = _lon_to_rad_cw(lon_start, asc_lon)
        x1,y1 = xy(R_SIGN_IN,  a)
        x2,y2 = xy(R_SIGN_OUT, a)
        ax.plot([x1,x2],[y1,y2], color='white', lw=2.0, zorder=5)

    # Обводки кола знаків
    add_circle(R_SIGN_OUT, '#555555', lw=2.5, zorder=7)
    add_circle(R_SIGN_IN,  '#555555', lw=2.0, zorder=7)

    # ─── Кола планет ──────────────────────────────────────────────────────────
    add_circle(R_TRANSIT_P, '#AAAAAA', lw=0.8, ls='--', zorder=4)
    add_circle(R_NATAL_P,   '#AAAAAA', lw=0.8, ls='--', zorder=4)
    add_circle(R_HOUSE_IN,  '#333333', lw=1.5, zorder=7)
    add_circle(R_CENTER,    '#333333', lw=2.0, zorder=10)

    # ─── Будинки ──────────────────────────────────────────────────────────────
    AXES = {0:'As', 3:'Ic', 6:'Ds', 9:'Mc'}
    AXIS_COLORS = {0:'#1565C0', 3:'#43A047', 6:'#1565C0', 9:'#F9A825'}

    for idx, cusp_lon in enumerate(cusps):
        a = _lon_to_rad_cw(cusp_lon, asc_lon)
        is_axis = idx in AXES
        col = AXIS_COLORS.get(idx, '#CCCCCC') if is_axis else '#DDDDDD'
        lw  = 2.5 if is_axis else 0.8

        x1,y1 = xy(R_HOUSE_IN,  a)
        x2,y2 = xy(R_HOUSE_OUT, a)
        ax.plot([x1,x2],[y1,y2], color=col, lw=lw, zorder=8)

        # Мітки осей зовні кільця знаків
        if is_axis:
            deg_in_sign = int(cusp_lon % 30)
            r_lbl = R_SIGN_OUT + 0.08
            lbl = f"{AXES[idx]}{deg_in_sign}"
            ax.text(*xy(r_lbl, a), lbl, ha='center', va='center',
                    fontsize=11, color='#222222', fontweight='bold',
                    zorder=15, fontfamily='DejaVu Sans')

        # Номери будинків і номери куспідів (зовні між sign_in і transit)
        next_lon = cusps[(idx+1) % 12]
        delta = (next_lon - cusp_lon) % 360
        mid_lon = cusp_lon + delta / 2
        am = _lon_to_rad_cw(mid_lon, asc_lon)
        # Номер будинку між natal і house_in
        r_n = (R_HOUSE_IN + R_ASP) / 2.0
        ax.text(*xy(r_n, am), str(idx+1), ha='center', va='center',
                fontsize=9, color='#888888', zorder=8, fontfamily='DejaVu Sans')

        # Номер куспіду між знаками та transit-колом
        r_c = (R_SIGN_IN + R_TRANSIT_P) / 2.0
        roman = ['I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII']
        cusp_deg = int(cusp_lon % 30)
        ax.text(*xy(r_c, am), f"{roman[idx]}\n{cusp_deg}", ha='center', va='center',
                fontsize=7.5, color='#555555', zorder=8, fontfamily='DejaVu Sans',
                linespacing=1.2)

    # ─── Аспекти натальних (чорні, жирні) ────────────────────────────────────
    for p1,p2,asp_name,orb_v in natal_aspects:
        i1 = natal_names.index(p1)
        i2 = natal_names.index(p2)
        a1 = _lon_to_rad_cw(natal_lons[i1], asc_lon)
        a2 = _lon_to_rad_cw(natal_lons[i2], asc_lon)
        x1,y1 = xy(R_ASP, a1)
        x2,y2 = xy(R_ASP, a2)
        lw    = 2.5 if asp_name in ('trine','opposition','conjunction') else 1.8
        alpha = max(0.3, 0.95 - orb_v*0.06)
        ls    = '--' if asp_name == 'square' else (':' if asp_name == 'quincunx' else '-')
        ax.plot([x1,x2],[y1,y2], color='#111111', lw=lw, alpha=alpha, ls=ls,
                zorder=6, solid_capstyle='round')

        # Символ аспекту по середині
        sym = ASP_SYMBOLS.get(asp_name,'')
        if sym:
            mx,my = (x1+x2)/2, (y1+y2)/2
            ax.text(mx, my, sym, ha='center', va='center',
                    fontsize=8, color='#333333', zorder=7,
                    fontfamily='DejaVu Sans',
                    bbox=dict(boxstyle='round,pad=0.1', facecolor='white', alpha=0.7, edgecolor='none'))

    # ─── Аспекти транзитів (червоні, жирні) ──────────────────────────────────
    for tp,np_,asp_name,orb_v in transit_aspects:
        if tp not in transit_names or np_ not in natal_names: continue
        ti = transit_names.index(tp)
        ni = natal_names.index(np_)
        a_t = _lon_to_rad_cw(transit_lons[ti], asc_lon)
        a_n = _lon_to_rad_cw(natal_lons[ni],   asc_lon)
        xt,yt = xy(R_ASP, a_t)
        xn,yn = xy(R_ASP, a_n)
        alpha = max(0.35, 0.95 - orb_v*0.09)
        ax.plot([xt,xn],[yt,yn], color='#E53935', lw=1.8, alpha=alpha,
                zorder=6, solid_capstyle='round')

        # Символ аспекту
        sym = ASP_SYMBOLS.get(asp_name,'')
        if sym:
            mx,my = (xt+xn)/2, (yt+yn)/2
            ax.text(mx, my, sym, ha='center', va='center',
                    fontsize=8, color='#E53935', zorder=7,
                    fontfamily='DejaVu Sans',
                    bbox=dict(boxstyle='round,pad=0.1', facecolor='white', alpha=0.7, edgecolor='none'))

    # ─── Натальні планети (між R_HOUSE_OUT і R_NATAL_P, чорні великі) ────────
    for i, key in enumerate(natal_names):
        lon_exact   = natal_lons[i]
        lon_display = natal_display[i]

        # Крапка на R_NATAL_P (точний градус) — жовто-зелена як референс
        a_exact = _lon_to_rad_cw(lon_exact, asc_lon)
        dx,dy = xy(R_NATAL_P, a_exact)
        ax.plot(dx, dy, 'o', ms=5, color='#9E9D24', zorder=11, markeredgecolor='#555555', markeredgewidth=0.5)

        # Символ: зовні від R_NATAL_P
        a_disp = _lon_to_rad_cw(lon_display, asc_lon)
        r_sym  = R_NATAL_P + 0.115
        sx,sy  = xy(r_sym, a_disp)

        # Лінія крапка→символ
        ax.plot([dx,sx],[dy,sy], color='#AAAAAA', lw=0.8, zorder=9)

        # Символ планети (великий, чорний)
        sym = PLANET_SYMBOLS.get(key, key[:2])
        ax.text(sx, sy, sym, ha='center', va='center',
                fontsize=19, color='#111111', fontweight='bold',
                zorder=13, fontfamily='DejaVu Sans')

        # Градус зверху символу
        deg_in_sign = int(lon_exact % 30)
        ax.text(sx, sy + 0.096, f"{deg_in_sign}", ha='center', va='center',
                fontsize=9, color='#333333', fontweight='bold',
                zorder=13, fontfamily='DejaVu Sans')

    # ─── Транзитні планети (між R_TRANSIT_P і R_SIGN_IN, червоні) ────────────
    # Два кільця для скупчених
    sorted_t = sorted(range(len(transit_display)), key=lambda i: transit_display[i])
    ring2 = set()
    prev_lon = None; tog = 0
    for idx in sorted_t:
        lon = transit_display[idx]
        if prev_lon is not None and (lon - prev_lon) % 360 < 11.0:
            tog = 1 - tog
        else:
            tog = 0
        if tog: ring2.add(idx)
        prev_lon = lon

    for i, key in enumerate(transit_names):
        lon_exact   = transit_lons[i]
        lon_display = transit_display[i]

        # Крапка на R_TRANSIT_P
        a_exact = _lon_to_rad_cw(lon_exact, asc_lon)
        dx,dy = xy(R_TRANSIT_P, a_exact)
        ax.plot(dx, dy, 'o', ms=4.5, color='#9E9D24', zorder=11,
                markeredgecolor='#555555', markeredgewidth=0.5)

        # Символ зовні R_TRANSIT_P (між transit і sign_in)
        a_disp = _lon_to_rad_cw(lon_display, asc_lon)
        r_base = R_TRANSIT_P + 0.10
        r_sym  = (r_base - 0.10) if i in ring2 else r_base
        sx,sy  = xy(r_sym, a_disp)

        ax.plot([dx,sx],[dy,sy], color='#FFAAAA', lw=0.7, zorder=9)

        sym = PLANET_SYMBOLS.get(key, key[:2])
        ax.text(sx, sy, sym, ha='center', va='center',
                fontsize=17, color='#C62828', fontweight='bold',
                zorder=13, fontfamily='DejaVu Sans')

        deg_in_sign = int(lon_exact % 30)
        ax.text(sx, sy + 0.088, f"{deg_in_sign}", ha='center', va='center',
                fontsize=8.5, color='#C62828', fontweight='bold',
                zorder=13, fontfamily='DejaVu Sans')

    # ─── Заголовок ────────────────────────────────────────────────────────────
    ax.text(0, 1.60,
            f"NATAL  +  TRANSIT  ·  {now_local.strftime('%d.%m.%Y %H:%M')}",
            ha='center', va='center', fontsize=13, color='#222222', fontweight='bold',
            zorder=20, fontfamily='DejaVu Sans')
    ax.text(0, -1.60,
            'Born: 22.09.1989  ·  02:52  ·  Lviv  ·  Placidus',
            ha='center', va='center', fontsize=9.5, color='#666666',
            zorder=20, fontfamily='DejaVu Sans')

    # ─── Легенда ──────────────────────────────────────────────────────────────
    ax.text(-1.58, -1.35, 'nt', fontsize=10, color='#111111', fontweight='bold',
            va='center', fontfamily='DejaVu Sans', zorder=20)
    ax.text(-1.40, -1.35, 'tr', fontsize=10, color='#C62828', fontweight='bold',
            va='center', fontfamily='DejaVu Sans', zorder=20)
    items = [
        ('#111111', '-',  '─── Trine / Sextile'),
        ('#111111', '--', '- - Square'),
        ('#E53935', '-',  '─── Transit aspects'),
    ]
    for k,(col,ls,lbl) in enumerate(items):
        y = -1.46 - k*0.09
        ax.plot([-1.58,-1.44],[y,y], color=col, lw=2.2, ls=ls, zorder=20, solid_capstyle='round')
        ax.text(-1.40, y, lbl, va='center', fontsize=8,
                color='#444444', fontfamily='DejaVu Sans', zorder=20)

    plt.savefig(output_path, dpi=220, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    return output_path


if __name__ == "__main__":
    path = generate_natal_chart("/tmp/test_chart.png")
    print(f"Saved: {path} ({os.path.getsize(path)//1024}K)")
