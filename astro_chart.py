#!/usr/bin/env python3
"""
Натальна карта + транзити. Висока чіткість, великі символи, читабельні аспекти.
"""
import os, math, tempfile, warnings
warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Arc
import numpy as np
from datetime import datetime, timezone, timedelta

try:
    from kerykeion import AstrologicalSubject
    _OK = True
except ImportError:
    _OK = False

# ─── Дані ────────────────────────────────────────────────────────────────────
BIRTH_YEAR, BIRTH_MONTH, BIRTH_DAY = 1989, 9, 22
BIRTH_HOUR, BIRTH_MIN = 2, 52
BIRTH_LAT, BIRTH_LON = 49.8383, 24.0233
BIRTH_TZ = "Europe/Kiev"
CURRENT_LAT, CURRENT_LON = 48.7136, 21.2581

SIGN_SYMBOLS = ['♈','♉','♊','♋','♌','♍','♎','♏','♐','♑','♒','♓']
# Кольори стихій: вогонь, земля, повітря, вода
SIGN_COLORS = [
    '#e74c3c','#8BC34A','#F9A825','#2196F3',  # Овен Телець Близ Рак
    '#e74c3c','#8BC34A','#F9A825','#2196F3',  # Лев Діва Терези Скорп
    '#e74c3c','#8BC34A','#F9A825','#2196F3',  # Стріл Козер Водол Риби
]

PLANET_SYMBOLS = {
    'sun':'☉','moon':'☽','mercury':'☿','venus':'♀','mars':'♂',
    'jupiter':'♃','saturn':'♄','uranus':'⛢','neptune':'♆','pluto':'♇',
}
PLANET_UA = {
    'sun':'Сонце','moon':'Місяць','mercury':'Меркурій','venus':'Венера','mars':'Марс',
    'jupiter':'Юпітер','saturn':'Сатурн','uranus':'Уран','neptune':'Нептун','pluto':'Плутон',
}

NATAL_COL   = '#FFD700'    # золотий
TRANSIT_COL = '#64B5F6'    # блакитний
BG = '#0A0E1A'             # темно-синій фон

ASP_COLORS = {
    'conjunction': '#FFC107',
    'trine':       '#4CAF50',
    'sextile':     '#8BC34A',
    'square':      '#F44336',
    'opposition':  '#E91E63',
    'quincunx':    '#9C27B0',
}
ASP_UA = {
    'conjunction':'Конюнкція','trine':'Трин','sextile':'Секстиль',
    'square':'Квадрат','opposition':'Опозиція','quincunx':'Квінкункс',
}

PLANETS_LIST = ['sun','moon','mercury','venus','mars','jupiter','saturn','uranus','neptune','pluto']


def _lon_to_rad(lon):
    """Еклиптична довгота → радіани для matplotlib (0°=праворуч, CCW)."""
    return math.radians(180.0 - lon)


def _get_aspects(lons_a, names_a, lons_b, names_b, orb=8.0, cross=False):
    ASPECTS = {0:'conjunction',60:'sextile',90:'square',120:'trine',150:'quincunx',180:'opposition'}
    ORB_MAP  = {0:8,60:6,90:7,120:8,150:3,180:8}
    result = []
    for i,(la,na) in enumerate(zip(lons_a,names_a)):
        for j,(lb,nb) in enumerate(zip(lons_b,names_b)):
            if not cross and i>=j: continue
            diff = abs(la-lb)%360
            if diff>180: diff=360-diff
            for deg,name in ASPECTS.items():
                o = min(orb, ORB_MAP[deg])
                if abs(diff-deg)<=o:
                    result.append((na,nb,name,abs(diff-deg)))
    return result


def _spread_planets(lons, min_gap=9.0):
    """Рознести планети що накладаються, зберігаючи порядок."""
    indexed = sorted(enumerate(lons), key=lambda x: x[1])
    display = list(lons)
    for _ in range(100):
        changed = False
        for k in range(len(indexed)):
            idx_a, _ = indexed[k]
            idx_b, _ = indexed[(k+1) % len(indexed)]
            diff = (display[idx_b] - display[idx_a]) % 360
            if diff < min_gap and diff >= 0:
                push = (min_gap - diff) / 2.0 + 0.1
                display[idx_a] = (display[idx_a] - push) % 360
                display[idx_b] = (display[idx_b] + push) % 360
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

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc + timedelta(hours=3)  # Київ (літо)

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

    # Рознести планети
    natal_display   = _spread_planets(natal_lons,   min_gap=9.0)
    transit_display = _spread_planets(transit_lons, min_gap=9.0)

    # ─── Полотно ─────────────────────────────────────────────────────────────
    SIZE = 18  # дюйми — велике!
    fig = plt.figure(figsize=(SIZE, SIZE), facecolor=BG)
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0], facecolor=BG)
    ax.set_xlim(-1.55, 1.55)
    ax.set_ylim(-1.65, 1.60)
    ax.set_aspect('equal')
    ax.axis('off')

    # ── Радіуси кіл ──────────────────────────────────────────────────────────
    R_OUTER     = 1.38   # зовнішній край знаків
    R_SIGN_IN   = 1.20   # внутрішній край кільця знаків
    R_NATAL_P   = 1.09   # кільце натальних планет
    R_CUSP_OUT  = R_SIGN_IN
    R_CUSP_IN   = 0.35   # лінії будинків до центру
    R_HOUSE_LBL = 1.13   # ← номер будинку (зовні, між куспідами)  
    R_ASPECT    = 0.78   # аспектні лінії натальних
    R_TRANSIT_P = 0.63   # кільце транзитних планет (зовнішнє)
    R_TRANSIT_P2= 0.50   # друге кільце (для натовпу планет)
    R_TRANSIT_A = 0.58   # аспектні лінії транзитів
    R_CENTER    = 0.28   # центральне коло

    def circle(r, color, lw=1.5, fill=False, fc=BG, alpha=1.0, zorder=2):
        c = plt.Circle((0,0), r, color=color, fill=fill, linewidth=lw,
                        facecolor=fc if fill else 'none', alpha=alpha, zorder=zorder)
        ax.add_patch(c)

    # Фонові кола
    circle(R_OUTER,    '#263244', lw=2.5, zorder=3)
    circle(R_SIGN_IN,  '#1a2840', lw=1.5, fill=True, fc='#101828', zorder=1)
    circle(R_SIGN_IN,  '#263244', lw=1.5, zorder=3)
    circle(R_NATAL_P,  '#1e3050', lw=1.0, alpha=0.5, zorder=3)
    circle(R_TRANSIT_P,'#142030', lw=1.0, alpha=0.5, zorder=3)
    circle(R_CENTER,   '#0A0E1A', lw=0, fill=True, fc='#0A0E1A', zorder=9)
    circle(R_CENTER,   '#263244', lw=2, zorder=10)

    # ── Знаки зодіаку ────────────────────────────────────────────────────────
    for i in range(12):
        lon_start = i * 30
        lon_mid   = lon_start + 15

        # Кольоровий сектор фону
        theta1 = 180 - (lon_start + 30)
        theta2 = 180 - lon_start
        wedge = mpatches.Wedge(
            (0,0), R_OUTER, theta1, theta2,
            width=(R_OUTER - R_SIGN_IN),
            color=SIGN_COLORS[i], alpha=0.10, zorder=2
        )
        ax.add_patch(wedge)

        # Розподільні лінії
        a = _lon_to_rad(lon_start)
        x1,y1 = R_SIGN_IN*math.cos(a), R_SIGN_IN*math.sin(a)
        x2,y2 = R_OUTER*math.cos(a),   R_OUTER*math.sin(a)
        ax.plot([x1,x2],[y1,y2], color='#2c4060', lw=1.0, zorder=4)

        # Символ знаку
        am = _lon_to_rad(lon_mid)
        r_mid = (R_SIGN_IN + R_OUTER) / 2.0
        ax.text(r_mid*math.cos(am), r_mid*math.sin(am),
                SIGN_SYMBOLS[i], ha='center', va='center',
                fontsize=20, color=SIGN_COLORS[i], fontweight='bold',
                zorder=6, fontfamily='DejaVu Sans')

    # ── Будинки ──────────────────────────────────────────────────────────────
    MAIN_AXES = (0, 3, 6, 9)
    for idx, cusp_lon in enumerate(cusps):
        a = _lon_to_rad(cusp_lon)
        is_main = idx in MAIN_AXES
        col = '#FFB300' if is_main else '#3d6080'
        lw  = 2.2 if is_main else 0.9
        x1,y1 = R_CUSP_IN*math.cos(a),  R_CUSP_IN*math.sin(a)
        x2,y2 = R_CUSP_OUT*math.cos(a), R_CUSP_OUT*math.sin(a)
        ax.plot([x1,x2],[y1,y2], color=col, lw=lw, zorder=5, alpha=0.9)

        # Мітка куспіда (I, IV, VII, X — великі)
        if is_main:
            labels = {0:'ASC',3:'IC',6:'DSC',9:'MC'}
            lbl_r = R_CUSP_IN - 0.08
            ax.text(lbl_r*math.cos(a), lbl_r*math.sin(a),
                    labels[idx], ha='center', va='center',
                    fontsize=9, color='#FFB300', fontweight='bold', zorder=12,
                    fontfamily='DejaVu Sans')

        # Номер будинку посередині між куспідами
        next_lon = cusps[(idx+1)%12]
        delta = (next_lon - cusp_lon) % 360
        mid_lon = cusp_lon + delta/2
        am = _lon_to_rad(mid_lon)
        r_lbl = (R_SIGN_IN + R_NATAL_P) / 2.0  # між знаками і планетами
        ax.text(r_lbl*math.cos(am), r_lbl*math.sin(am),
                str(idx+1), ha='center', va='center',
                fontsize=8, color='#607D8B', fontweight='bold', zorder=6,
                fontfamily='DejaVu Sans')

    # ── Аспектні лінії натальних ─────────────────────────────────────────────
    for p1,p2,asp_name,orb_v in natal_aspects:
        i1 = natal_names.index(p1)
        i2 = natal_names.index(p2)
        a1 = _lon_to_rad(natal_lons[i1])
        a2 = _lon_to_rad(natal_lons[i2])
        x1,y1 = R_ASPECT*math.cos(a1), R_ASPECT*math.sin(a1)
        x2,y2 = R_ASPECT*math.cos(a2), R_ASPECT*math.sin(a2)
        col = ASP_COLORS.get(asp_name,'#607D8B')
        lw  = 2.2 if asp_name in ('conjunction','trine','opposition') else 1.5
        alpha = max(0.35, 0.95 - orb_v*0.07)
        ls = '--' if asp_name == 'square' else (':' if asp_name == 'quincunx' else '-')
        ax.plot([x1,x2],[y1,y2], color=col, lw=lw, alpha=alpha, linestyle=ls, zorder=5, solid_capstyle='round')

    # ── Аспектні лінії транзитів до натальних ────────────────────────────────
    for tp,np_,asp_name,orb_v in transit_aspects:
        if tp not in transit_names or np_ not in natal_names: continue
        ti = transit_names.index(tp)
        ni = natal_names.index(np_)
        a_t = _lon_to_rad(transit_lons[ti])
        a_n = _lon_to_rad(natal_lons[ni])
        xt,yt = R_TRANSIT_A*math.cos(a_t), R_TRANSIT_A*math.sin(a_t)
        xn,yn = R_ASPECT*math.cos(a_n),    R_ASPECT*math.sin(a_n)
        col = ASP_COLORS.get(asp_name,'#607D8B')
        alpha = max(0.3, 0.85 - orb_v*0.10)
        ax.plot([xt,xn],[yt,yn], color=col, lw=1.6, alpha=alpha,
                linestyle='-.', zorder=5, solid_capstyle='round')

    # ── Натальні планети (зовнішнє кільце, золоті) ───────────────────────────
    for i, key in enumerate(natal_names):
        lon_display = natal_display[i]
        a = _lon_to_rad(lon_display)
        px,py = R_NATAL_P*math.cos(a), R_NATAL_P*math.sin(a)

        # Лінія-підводка до точного градуса
        a_exact = _lon_to_rad(natal_lons[i])
        xe,ye = R_SIGN_IN*math.cos(a_exact), R_SIGN_IN*math.sin(a_exact)
        ax.plot([px,xe],[py,ye], color=NATAL_COL, lw=0.6, alpha=0.25, zorder=6)

        # Кружок планети
        bg = plt.Circle((px,py), 0.072, color='#0f1a2b', zorder=11)
        ax.add_patch(bg)
        border = plt.Circle((px,py), 0.072, color=NATAL_COL, fill=False, lw=2.0, zorder=12)
        ax.add_patch(border)

        sym = PLANET_SYMBOLS.get(key, key[:2])
        ax.text(px, py, sym, ha='center', va='center',
                fontsize=13, color=NATAL_COL, fontweight='bold',
                zorder=13, fontfamily='DejaVu Sans')

        # Градуси (маленько, знизу кружка)
        deg_in_sign = natal_lons[i] % 30
        ax.text(px, py-0.100, f"{int(deg_in_sign)}°", ha='center', va='center',
                fontsize=6.5, color='#B8860B', zorder=13, fontfamily='DejaVu Sans')

    # ── Транзитні планети (2 кільця шахово для скупчених) ────────────────────
    # Знайдемо групи близьких планет і розмістимо їх на двох радіусах
    sorted_t_idx = sorted(range(len(transit_display)), key=lambda i: transit_display[i])
    ring_assignment = {}  # index -> R
    prev_lon = None
    toggle = 0
    for idx in sorted_t_idx:
        lon = transit_display[idx]
        if prev_lon is not None and (lon - prev_lon) % 360 < 12.0:
            toggle = 1 - toggle
        else:
            toggle = 0
        ring_assignment[idx] = R_TRANSIT_P2 if toggle else R_TRANSIT_P
        prev_lon = lon

    for i, key in enumerate(transit_names):
        lon_display = transit_display[i]
        a = _lon_to_rad(lon_display)
        r_use = ring_assignment.get(i, R_TRANSIT_P)
        px,py = r_use*math.cos(a), r_use*math.sin(a)

        bg = plt.Circle((px,py), 0.068, color='#070d18', zorder=11)
        ax.add_patch(bg)
        border = plt.Circle((px,py), 0.068, color=TRANSIT_COL, fill=False, lw=2.0, zorder=12)
        ax.add_patch(border)

        sym = PLANET_SYMBOLS.get(key, key[:2])
        ax.text(px, py, sym, ha='center', va='center',
                fontsize=12, color=TRANSIT_COL, fontweight='bold',
                zorder=13, fontfamily='DejaVu Sans')

        deg_in_sign = transit_lons[i] % 30
        ax.text(px, py-0.097, f"{int(deg_in_sign)}°", ha='center', va='center',
                fontsize=6.5, color='#1565C0', zorder=13, fontfamily='DejaVu Sans')

    # ── Заголовок ────────────────────────────────────────────────────────────
    ax.text(0, 1.53, f"☉ НАТАЛЬНА КАРТА  +  ТРАНЗИТИ  ·  {now_local.strftime('%d.%m.%Y %H:%M')}",
            ha='center', va='center', fontsize=13, color='#ECF0F1', fontweight='bold',
            zorder=20, fontfamily='DejaVu Sans')
    ax.text(0, -1.55, 'Народження: 22.09.1989  ·  02:52  ·  Львів  ·  Пласідус',
            ha='center', va='center', fontsize=9, color='#546E7A',
            zorder=20, fontfamily='DejaVu Sans')

    # ── Легенда аспектів (ліво-знизу) ────────────────────────────────────────
    legend_x = -1.52
    legend_items = [
        (ASP_COLORS['conjunction'], '-',  'Конюнкція  0°'),
        (ASP_COLORS['trine'],       '-',  'Трин  120°'),
        (ASP_COLORS['sextile'],     '-',  'Секстиль  60°'),
        (ASP_COLORS['square'],      '--', 'Квадрат  90°'),
        (ASP_COLORS['opposition'],  '-',  'Опозиція  180°'),
        (ASP_COLORS['quincunx'],    ':',  'Квінкункс  150°'),
    ]
    ax.text(legend_x, -1.08, 'АСПЕКТИ', fontsize=8, color='#78909C', fontweight='bold',
            va='center', fontfamily='DejaVu Sans', zorder=20)
    for k,(col,ls,label) in enumerate(legend_items):
        ly = -1.18 - k*0.097
        ax.plot([legend_x, legend_x+0.14],[ly,ly], color=col, lw=2.5, ls=ls, zorder=20,
                solid_capstyle='round')
        ax.text(legend_x+0.18, ly, label, va='center', fontsize=7.5,
                color='#90A4AE', fontfamily='DejaVu Sans', zorder=20)

    # ── Легенда кілець (право-знизу) ─────────────────────────────────────────
    rx = 0.90
    ax.text(rx, -1.08, 'КІЛЬЦЯ', fontsize=8, color='#78909C', fontweight='bold',
            va='center', fontfamily='DejaVu Sans', zorder=20)
    ax.plot([rx, rx+0.14],[-1.18,-1.18], color=NATAL_COL, lw=2.5, zorder=20)
    ax.text(rx+0.18, -1.18, 'Натальні планети', va='center', fontsize=7.5,
            color=NATAL_COL, fontfamily='DejaVu Sans', zorder=20)
    ax.plot([rx, rx+0.14],[-1.28,-1.28], color=TRANSIT_COL, lw=2.5, ls='--', zorder=20)
    ax.text(rx+0.18, -1.28, 'Транзити сьогодні', va='center', fontsize=7.5,
            color=TRANSIT_COL, fontfamily='DejaVu Sans', zorder=20)

    # ── Список активних транзит-аспектів (знизу по центру) ───────────────────
    active = [(tp,np_,aname,orb) for tp,np_,aname,orb in transit_aspects
              if aname in ('conjunction','trine','square','opposition') and orb < 3.5]
    active.sort(key=lambda x: x[3])
    if active:
        ax.text(0.42, -1.08, 'АКТИВНІ ТРАНЗИТИ', ha='center', fontsize=8,
                color='#78909C', fontweight='bold', zorder=20, fontfamily='DejaVu Sans')
        for k,(tp,np_,aname,orb) in enumerate(active[:6]):
            col = ASP_COLORS.get(aname,'#607D8B')
            sym_t = PLANET_SYMBOLS.get(tp,'?')
            sym_n = PLANET_SYMBOLS.get(np_,'?')
            asp_sym = {'conjunction':'☌','trine':'△','square':'□','opposition':'☍'}.get(aname,'*')
            txt = f"{sym_t}{asp_sym}{sym_n}  {orb:.1f}°  {ASP_UA.get(aname,'')}"
            ax.text(0.42, -1.18 - k*0.092, txt, ha='center', va='center',
                    fontsize=7.5, color=col, zorder=20, fontfamily='DejaVu Sans')

    plt.savefig(output_path, dpi=200, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    plt.close(fig)
    return output_path


if __name__ == "__main__":
    path = generate_natal_chart("/tmp/test_chart.png")
    print(f"Saved: {path} ({os.path.getsize(path)//1024}K)")
