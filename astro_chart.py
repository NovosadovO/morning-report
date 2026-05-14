#!/usr/bin/env python3
"""
Натальна карта + транзити — класичний стиль (білий фон, кольорові знаки, чорні аспекти).
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
SIGN_NAMES   = ['Aries','Taurus','Gemini','Cancer','Leo','Virgo',
                'Libra','Scorpio','Sagitt','Capric','Aquar','Pisces']

# Стихії: вогонь=червоний, земля=зелений, повітря=жовтий, вода=синій
ELEMENT_COLORS = {
    'fire':  '#E53935',
    'earth': '#43A047',
    'air':   '#FFB300',
    'water': '#1E88E5',
}
SIGN_ELEMENT = [
    'fire','earth','air','water',
    'fire','earth','air','water',
    'fire','earth','air','water',
]

PLANET_SYMBOLS = {
    'sun':'☉','moon':'☽','mercury':'☿','venus':'♀','mars':'♂',
    'jupiter':'♃','saturn':'♄','uranus':'⛢','neptune':'♆','pluto':'♇',
}

ASP_ANGLES = {0:'conjunction',60:'sextile',90:'square',120:'trine',150:'quincunx',180:'opposition'}
ASP_ORB    = {0:8, 60:6, 90:7, 120:8, 150:3, 180:8}
# Натальні: чорні; транзити: червоні
ASP_NATAL_COLORS   = {'conjunction':'#000000','trine':'#000000','sextile':'#000000',
                      'square':'#000000','opposition':'#000000','quincunx':'#888888'}
ASP_TRANSIT_COLORS = {'conjunction':'#E53935','trine':'#E53935','sextile':'#E53935',
                      'square':'#E53935','opposition':'#E53935','quincunx':'#E53935'}

ASP_SYMBOLS = {'conjunction':'☌','trine':'△','sextile':'*','square':'□','opposition':'☍','quincunx':'⚻'}

PLANETS_LIST = ['sun','moon','mercury','venus','mars','jupiter','saturn','uranus','neptune','pluto']


def _lon_to_rad(lon):
    return math.radians(180.0 - lon)


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


def _spread_planets(lons, min_gap=8.0):
    indexed = sorted(enumerate(lons), key=lambda x: x[1])
    display = list(lons)
    for _ in range(200):
        changed = False
        for k in range(len(indexed)):
            ia, _ = indexed[k]
            ib, _ = indexed[(k+1) % len(indexed)]
            diff = (display[ib] - display[ia]) % 360
            if 0 < diff < min_gap:
                push = (min_gap - diff) / 2.0 + 0.2
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
    now_local = now_utc + timedelta(hours=3)

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

    natal_display   = _spread_planets(natal_lons,   min_gap=11.0)
    transit_display = _spread_planets(transit_lons, min_gap=11.0)

    # ─── Полотно ──────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 16), facecolor='white')
    ax  = fig.add_axes([0.04, 0.04, 0.92, 0.92], facecolor='white')
    ax.set_xlim(-1.55, 1.55)
    ax.set_ylim(-1.60, 1.60)
    ax.set_aspect('equal')
    ax.axis('off')

    # ─── Радіуси ──────────────────────────────────────────────────────────────
    R_OUT        = 1.42   # зовнішній край кольорового кільця знаків
    R_SIGN_OUT   = 1.42
    R_SIGN_IN    = 1.18   # внутрішній край кільця знаків
    R_NATAL_DOT  = 1.13   # крапки натальних планет на колі
    R_NATAL_LBL  = 1.30   # символи натальних планет (зовні)
    R_HOUSE_LBL  = 1.09   # номери будинків
    R_INNER      = 0.80   # внутрішнє коло (куспіди від нього до знаків)
    R_TRANSIT_DOT= 0.76   # крапки транзитів
    R_TRANSIT_LBL= 0.64   # символи транзитів (всередині)
    R_ASP_NATAL  = 0.76   # аспектні лінії натальних
    R_ASP_TRANSIT= 0.72   # аспектні лінії транзитів
    R_CENTER     = 0.26   # центральне коло

    def add_circle(r, color='#333333', lw=1.0, fill=False, fc='white', alpha=1.0, zorder=2, ls='-'):
        c = plt.Circle((0,0), r, color=color, fill=fill, linewidth=lw,
                        facecolor=fc if fill else 'none', alpha=alpha, zorder=zorder, linestyle=ls)
        ax.add_patch(c)

    # ─── Кольорові сектори знаків ─────────────────────────────────────────────
    for i in range(12):
        lon_start = i * 30
        t1 = 180 - (lon_start + 30)
        t2 = 180 - lon_start
        col = ELEMENT_COLORS[SIGN_ELEMENT[i]]
        wedge = mpatches.Wedge(
            (0,0), R_SIGN_OUT, t1, t2,
            width=(R_SIGN_OUT - R_SIGN_IN),
            color=col, alpha=1.0, zorder=3
        )
        ax.add_patch(wedge)

        # Символ знаку (великий, білий)
        lon_mid = lon_start + 15
        am = _lon_to_rad(lon_mid)
        rm = (R_SIGN_IN + R_SIGN_OUT) / 2.0
        ax.text(rm*math.cos(am), rm*math.sin(am),
                SIGN_SYMBOLS[i], ha='center', va='center',
                fontsize=22, color='white', fontweight='bold',
                zorder=6, fontfamily='DejaVu Sans')

        # Розподільна лінія між знаками
        a = _lon_to_rad(lon_start)
        x1,y1 = R_SIGN_IN*math.cos(a), R_SIGN_IN*math.sin(a)
        x2,y2 = R_SIGN_OUT*math.cos(a), R_SIGN_OUT*math.sin(a)
        ax.plot([x1,x2],[y1,y2], color='white', lw=1.5, zorder=5)

    # Обводки кільця знаків
    add_circle(R_SIGN_OUT, '#555555', lw=2.0, zorder=7)
    add_circle(R_SIGN_IN,  '#555555', lw=1.5, zorder=7)

    # ─── Внутрішнє коло (куспіди) ─────────────────────────────────────────────
    add_circle(R_INNER, '#888888', lw=1.0, zorder=7)
    add_circle(R_CENTER,'#333333', lw=2.0, zorder=10)

    # ─── Будинки ──────────────────────────────────────────────────────────────
    AXES_IDX = {0:'As', 3:'Ic', 6:'Ds', 9:'Mc'}
    for idx, cusp_lon in enumerate(cusps):
        a = _lon_to_rad(cusp_lon)
        is_axis = idx in AXES_IDX
        col = '#000000' if is_axis else '#AAAAAA'
        lw  = 2.0 if is_axis else 0.7
        x1,y1 = R_CENTER*math.cos(a), R_CENTER*math.sin(a)
        x2,y2 = R_INNER*math.cos(a),  R_INNER*math.sin(a)
        ax.plot([x1,x2],[y1,y2], color=col, lw=lw, zorder=8)

        # Подовжити осі до знаків
        if is_axis:
            x3,y3 = R_SIGN_IN*math.cos(a), R_SIGN_IN*math.sin(a)
            # Колір осі: ASC/DSC-синій, MC-жовтий, IC-зелений
            axis_colors = {0:'#1565C0', 3:'#388E3C', 6:'#1565C0', 9:'#F9A825'}
            ax.plot([x1,x3],[y1,y3], color=axis_colors[idx], lw=2.5, zorder=9)

        # Номер будинку між куспідами
        next_lon = cusps[(idx+1) % 12]
        delta = (next_lon - cusp_lon) % 360
        mid_lon = cusp_lon + delta / 2
        am = _lon_to_rad(mid_lon)
        r_lbl = (R_SIGN_IN + R_INNER) / 2.0
        ax.text(r_lbl*math.cos(am), r_lbl*math.sin(am),
                str(idx+1), ha='center', va='center',
                fontsize=9, color='#555555', fontweight='bold', zorder=8,
                fontfamily='DejaVu Sans')

        # Підпис осі (As/Ds/Mc/Ic) та градус
        if is_axis:
            deg_in_sign = int(cusp_lon % 30)
            sign_idx    = int(cusp_lon // 30) % 12
            lbl = f"{AXES_IDX[idx]}{deg_in_sign}"
            # Зовні кільця знаків
            r_out_lbl = R_SIGN_OUT + 0.06
            ax.text(r_out_lbl*math.cos(a), r_out_lbl*math.sin(a),
                    lbl, ha='center', va='center',
                    fontsize=10, color='#222222', fontweight='bold', zorder=12,
                    fontfamily='DejaVu Sans')

    # ─── Аспекти натальних (чорні) ────────────────────────────────────────────
    for p1,p2,asp_name,orb_v in natal_aspects:
        i1 = natal_names.index(p1)
        i2 = natal_names.index(p2)
        a1 = _lon_to_rad(natal_lons[i1])
        a2 = _lon_to_rad(natal_lons[i2])
        x1,y1 = R_ASP_NATAL*math.cos(a1), R_ASP_NATAL*math.sin(a1)
        x2,y2 = R_ASP_NATAL*math.cos(a2), R_ASP_NATAL*math.sin(a2)
        col   = '#000000'
        lw    = 1.8 if asp_name in ('conjunction','trine','opposition') else 1.2
        alpha = max(0.25, 0.9 - orb_v*0.07)
        ls    = '--' if asp_name == 'square' else (':' if asp_name == 'quincunx' else '-')
        ax.plot([x1,x2],[y1,y2], color=col, lw=lw, alpha=alpha, ls=ls, zorder=6, solid_capstyle='round')

    # ─── Аспекти транзитів (червоні) ─────────────────────────────────────────
    for tp,np_,asp_name,orb_v in transit_aspects:
        if tp not in transit_names or np_ not in natal_names: continue
        ti = transit_names.index(tp)
        ni = natal_names.index(np_)
        a_t = _lon_to_rad(transit_lons[ti])
        a_n = _lon_to_rad(natal_lons[ni])
        xt,yt = R_ASP_TRANSIT*math.cos(a_t), R_ASP_TRANSIT*math.sin(a_t)
        xn,yn = R_ASP_NATAL*math.cos(a_n),   R_ASP_NATAL*math.sin(a_n)
        alpha = max(0.3, 0.9 - orb_v*0.10)
        ax.plot([xt,xn],[yt,yn], color='#E53935', lw=1.4, alpha=alpha,
                zorder=6, solid_capstyle='round')

        # Символ аспекту в середині лінії
        mx, my = (xt+xn)/2, (yt+yn)/2
        sym = ASP_SYMBOLS.get(asp_name,'')
        ax.text(mx, my, sym, ha='center', va='center',
                fontsize=7, color='#E53935', zorder=7, fontfamily='DejaVu Sans')

    # ─── Натальні планети (зовні R_INNER, чорні) ─────────────────────────────
    for i, key in enumerate(natal_names):
        lon_exact   = natal_lons[i]
        lon_display = natal_display[i]

        # Крапка на колі R_NATAL_DOT (точний градус)
        a_exact = _lon_to_rad(lon_exact)
        dx,dy = R_NATAL_DOT*math.cos(a_exact), R_NATAL_DOT*math.sin(a_exact)
        ax.plot(dx, dy, 'o', ms=4, color='#888888', zorder=10)

        # Лінія від крапки до символу
        a_disp = _lon_to_rad(lon_display)
        sx,sy = R_NATAL_LBL*math.cos(a_disp), R_NATAL_LBL*math.sin(a_disp)
        ax.plot([dx, sx],[dy, sy], color='#AAAAAA', lw=0.7, zorder=9)

        # Символ планети
        sym = PLANET_SYMBOLS.get(key, key[:2])
        ax.text(sx, sy, sym, ha='center', va='center',
                fontsize=16, color='#111111', fontweight='bold',
                zorder=12, fontfamily='DejaVu Sans')

        # Градус (зверху символу)
        deg_in_sign = int(lon_exact % 30)
        ax.text(sx, sy + 0.085, f"{deg_in_sign}", ha='center', va='center',
                fontsize=8, color='#333333', zorder=12, fontfamily='DejaVu Sans')

    # ─── Транзитні планети (всередині R_INNER, червоні) ──────────────────────
    # Два кільця для скупчень
    sorted_t = sorted(range(len(transit_display)), key=lambda i: transit_display[i])
    ring2 = set()
    prev_lon = None
    tog = 0
    for idx in sorted_t:
        lon = transit_display[idx]
        if prev_lon is not None and (lon - prev_lon) % 360 < 10.0:
            tog = 1 - tog
        else:
            tog = 0
        if tog:
            ring2.add(idx)
        prev_lon = lon

    for i, key in enumerate(transit_names):
        lon_exact   = transit_lons[i]
        lon_display = transit_display[i]
        r_lbl = (R_TRANSIT_LBL - 0.12) if i in ring2 else R_TRANSIT_LBL

        # Крапка на R_TRANSIT_DOT
        a_exact = _lon_to_rad(lon_exact)
        dx,dy = R_TRANSIT_DOT*math.cos(a_exact), R_TRANSIT_DOT*math.sin(a_exact)
        ax.plot(dx, dy, 'o', ms=3.5, color='#E53935', zorder=10, alpha=0.7)

        # Лінія до символу
        a_disp = _lon_to_rad(lon_display)
        sx,sy = r_lbl*math.cos(a_disp), r_lbl*math.sin(a_disp)
        ax.plot([dx,sx],[dy,sy], color='#FFAAAA', lw=0.6, zorder=9)

        # Символ
        sym = PLANET_SYMBOLS.get(key, key[:2])
        ax.text(sx, sy, sym, ha='center', va='center',
                fontsize=14, color='#C62828', fontweight='bold',
                zorder=12, fontfamily='DejaVu Sans')

        # Градус
        deg_in_sign = int(lon_exact % 30)
        ax.text(sx, sy + 0.078, f"{deg_in_sign}", ha='center', va='center',
                fontsize=7.5, color='#C62828', zorder=12, fontfamily='DejaVu Sans')

    # ─── Заголовок ────────────────────────────────────────────────────────────
    ax.text(0, 1.55, f"NATAL  +  TRANSIT  ·  {now_local.strftime('%d.%m.%Y %H:%M')}",
            ha='center', va='center', fontsize=13, color='#222222', fontweight='bold',
            zorder=20, fontfamily='DejaVu Sans')
    ax.text(0, -1.54, 'Born: 22.09.1989  ·  02:52  ·  Lviv  ·  Placidus',
            ha='center', va='center', fontsize=9.5, color='#666666',
            zorder=20, fontfamily='DejaVu Sans')

    # ─── Легенда (лівий нижній кут) ───────────────────────────────────────────
    lx, ly0 = -1.52, -1.10
    ax.text(lx, ly0, 'nt = natal   tr = transit', va='center', fontsize=8,
            color='#555555', fontfamily='DejaVu Sans', zorder=20)
    legend_asp = [
        ('#000000', '-',  '─── Trine / Sextile'),
        ('#000000', '--', '- - Square'),
        ('#E53935', '-',  '─── Transit aspects'),
    ]
    for k,(col,ls,lbl) in enumerate(legend_asp):
        y = ly0 - 0.10 - k*0.09
        ax.plot([lx, lx+0.14],[y,y], color=col, lw=2, ls=ls, zorder=20)
        ax.text(lx+0.18, y, lbl, va='center', fontsize=7.5,
                color='#444444', fontfamily='DejaVu Sans', zorder=20)

    plt.savefig(output_path, dpi=220, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    return output_path


if __name__ == "__main__":
    path = generate_natal_chart("/tmp/test_chart.png")
    print(f"Saved: {path} ({os.path.getsize(path)//1024}K)")
