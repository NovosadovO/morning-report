#!/usr/bin/env python3
"""
Генерує зображення натальної карти + транзити з виділеними аспектами та будинками.
Повертає шлях до PNG файлу.
"""
import os, math, tempfile, warnings
warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyArrowPatch, Arc
import numpy as np
from datetime import datetime, timezone, timedelta

try:
    from kerykeion import AstrologicalSubject
    _OK = True
except ImportError:
    _OK = False

# ─── Дані народження ─────────────────────────────────────────────────────────
BIRTH_YEAR, BIRTH_MONTH, BIRTH_DAY = 1989, 9, 22
BIRTH_HOUR, BIRTH_MIN = 2, 52
BIRTH_LAT, BIRTH_LON = 49.8383, 24.0233
BIRTH_TZ = "Europe/Kiev"
CURRENT_LAT, CURRENT_LON = 48.7136, 21.2581

SIGN_SYMBOLS = ['♈','♉','♊','♋','♌','♍','♎','♏','♐','♑','♒','♓']
SIGN_NAMES_UA = ['Овен','Телець','Близнюки','Рак','Лев','Діва','Терези','Скорпіон','Стрілець','Козеріг','Водолій','Риби']

SIGN_COLORS = [
    '#e74c3c','#2ecc71','#f39c12','#3498db',
    '#e74c3c','#2ecc71','#f39c12','#3498db',
    '#e74c3c','#2ecc71','#f39c12','#3498db',
]

PLANET_SYMBOLS = {
    'sun': '☉', 'moon': '☽', 'mercury': '☿', 'venus': '♀',
    'mars': '♂', 'jupiter': '♃', 'saturn': '♄',
    'uranus': '⛢', 'neptune': '♆', 'pluto': '♇',
}

NATAL_COLOR   = '#f1c40f'   # золотий — натальні
TRANSIT_COLOR = '#3498db'   # синій — транзитні
ASP_COLORS = {
    'conjunction': '#f39c12',
    'trine':       '#2ecc71',
    'sextile':     '#27ae60',
    'square':      '#e74c3c',
    'opposition':  '#c0392b',
    'quincunx':    '#9b59b6',
}

PLANETS_LIST = ['sun','moon','mercury','venus','mars','jupiter','saturn','uranus','neptune','pluto']

def _lon_to_angle(lon):
    """Конвертує еклиптичну довготу (0..360) в кут на колі (радіани), 0° = правий бік, CCW."""
    return math.radians(180 - lon)

def _get_aspects(lons_a, names_a, lons_b, names_b, orb=8.0, cross=False):
    """Аспекти між двома наборами планет."""
    ASPECTS = {0: 'conjunction', 60: 'sextile', 90: 'square', 120: 'trine', 150: 'quincunx', 180: 'opposition'}
    result = []
    for i, (la, na) in enumerate(zip(lons_a, names_a)):
        for j, (lb, nb) in enumerate(zip(lons_b, names_b)):
            if not cross and i >= j:
                continue
            diff = abs(la - lb) % 360
            if diff > 180:
                diff = 360 - diff
            for deg, name in ASPECTS.items():
                o = min(orb, 8 if deg in (0,180) else 6 if deg in (60,120,90) else 4)
                if abs(diff - deg) <= o:
                    result.append((na, nb, name, abs(diff - deg)))
    return result


def generate_natal_chart(output_path=None):
    """Генерує PNG з натальною картою + транзити. Повертає шлях."""
    if not _OK:
        return None

    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        output_path = tmp.name
        tmp.close()

    now_utc = datetime.now(timezone.utc)

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

    # Будинки (куспіди)
    house_keys = ['first_house','second_house','third_house','fourth_house','fifth_house','sixth_house',
                  'seventh_house','eighth_house','ninth_house','tenth_house','eleventh_house','twelfth_house']
    cusps = [getattr(natal, k).abs_pos for k in house_keys]

    # Натальні планети
    natal_lons, natal_names = [], []
    for key in PLANETS_LIST:
        try:
            p = getattr(natal, key)
            natal_lons.append(p.abs_pos)
            natal_names.append(key)
        except: pass

    # Транзитні планети
    transit_lons, transit_names = [], []
    for key in PLANETS_LIST:
        try:
            p = getattr(transit, key)
            transit_lons.append(p.abs_pos)
            transit_names.append(key)
        except: pass

    # Аспекти натальних між собою
    natal_aspects = _get_aspects(natal_lons, natal_names, natal_lons, natal_names, orb=8)
    # Транзити до натальних
    transit_aspects = _get_aspects(transit_lons, transit_names, natal_lons, natal_names, orb=5, cross=True)

    # ─── Малювання ───────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(12, 12), facecolor='#0d1117')
    ax = fig.add_axes([0, 0, 1, 1], facecolor='#0d1117')
    ax.set_xlim(-1.5, 1.5)
    ax.set_ylim(-1.5, 1.5)
    ax.set_aspect('equal')
    ax.axis('off')

    # Зовнішнє кільце знаків зодіаку
    outer_r = 1.35
    sign_r  = 1.28
    house_r = 1.15
    asp_r   = 0.75  # радіус для ліній аспектів (натальні)
    transit_asp_r = 0.72

    # Зовнішнє коло
    outer_circle = plt.Circle((0,0), outer_r, color='#2c3e50', fill=False, linewidth=2)
    ax.add_patch(outer_circle)

    # Кільце знаків
    sign_inner = plt.Circle((0,0), 1.20, color='#1a252f', fill=True, zorder=1)
    ax.add_patch(sign_inner)
    sign_outer = plt.Circle((0,0), outer_r, color='#2c3e50', fill=False, linewidth=2, zorder=2)
    ax.add_patch(sign_outer)
    sign_inner_c = plt.Circle((0,0), 1.20, color='#2c3e50', fill=False, linewidth=1.5, zorder=2)
    ax.add_patch(sign_inner_c)

    # Знаки зодіаку (30° кожен)
    for i in range(12):
        lon_start = i * 30
        lon_mid   = lon_start + 15
        # Лінія-розподільник між знаками
        angle_line = _lon_to_angle(lon_start)
        x1 = 1.20 * math.cos(angle_line)
        y1 = 1.20 * math.sin(angle_line)
        x2 = outer_r * math.cos(angle_line)
        y2 = outer_r * math.sin(angle_line)
        ax.plot([x1, x2], [y1, y2], color='#34495e', linewidth=0.8, zorder=3)

        # Символ знаку
        angle_mid = _lon_to_angle(lon_mid)
        tx = sign_r * math.cos(angle_mid)
        ty = sign_r * math.sin(angle_mid)
        ax.text(tx, ty, SIGN_SYMBOLS[i], ha='center', va='center',
                fontsize=14, color=SIGN_COLORS[i], fontweight='bold', zorder=5,
                fontfamily='DejaVu Sans')

    # Будинки (куспіди) — лінії всередині від house_r до center
    house_labels_r = 1.10
    for idx, cusp_lon in enumerate(cusps):
        angle = _lon_to_angle(cusp_lon)
        # Лінія куспіду
        lw = 2.5 if idx in (0, 3, 6, 9) else 0.8
        col = '#f39c12' if idx in (0, 3, 6, 9) else '#4a6278'
        x1 = 0.35 * math.cos(angle)
        y1 = 0.35 * math.sin(angle)
        x2 = 1.20 * math.cos(angle)
        y2 = 1.20 * math.sin(angle)
        ax.plot([x1, x2], [y1, y2], color=col, linewidth=lw, zorder=3, alpha=0.8)

        # Номер будинку посередині між куспідами
        next_cusp = cusps[(idx + 1) % 12]
        mid_lon = cusp_lon + ((next_cusp - cusp_lon) % 360) / 2
        mid_angle = _lon_to_angle(mid_lon)
        hx = house_labels_r * math.cos(mid_angle)
        hy = house_labels_r * math.sin(mid_angle)
        ax.text(hx, hy, str(idx + 1), ha='center', va='center',
                fontsize=8, color='#7f8c8d', zorder=5, fontfamily='DejaVu Sans')

    # Кола планет
    natal_circle = plt.Circle((0,0), 1.02, color='#1a252f', fill=False, linewidth=1.5, linestyle='--', zorder=3, alpha=0.6)
    ax.add_patch(natal_circle)
    transit_circle = plt.Circle((0,0), 0.88, color='#1a252f', fill=False, linewidth=1.5, linestyle=':', zorder=3, alpha=0.6)
    ax.add_patch(transit_circle)

    # Центральне коло
    center_c = plt.Circle((0,0), 0.30, color='#0d1117', fill=True, zorder=8)
    ax.add_patch(center_c)
    center_c2 = plt.Circle((0,0), 0.30, color='#2c3e50', fill=False, linewidth=2, zorder=9)
    ax.add_patch(center_c2)

    # Аспекти натальних між собою (внутрішні лінії)
    for p1, p2, asp_name, orb_val in natal_aspects:
        i1 = natal_names.index(p1)
        i2 = natal_names.index(p2)
        a1 = _lon_to_angle(natal_lons[i1])
        a2 = _lon_to_angle(natal_lons[i2])
        x1 = asp_r * math.cos(a1); y1 = asp_r * math.sin(a1)
        x2 = asp_r * math.cos(a2); y2 = asp_r * math.sin(a2)
        col = ASP_COLORS.get(asp_name, '#7f8c8d')
        lw  = 1.5 if asp_name in ('trine','sextile') else (2.0 if asp_name == 'conjunction' else 1.2)
        alpha = max(0.25, 0.8 - orb_val * 0.08)
        ls = '-' if asp_name in ('conjunction','trine','opposition','sextile') else '--'
        ax.plot([x1, x2], [y1, y2], color=col, linewidth=lw, alpha=alpha, linestyle=ls, zorder=4)

    # Аспекти транзитів до натальних (пунктир, ближче до центру)
    for tp, np_, asp_name, orb_val in transit_aspects:
        if tp not in transit_names or np_ not in natal_names: continue
        ti = transit_names.index(tp)
        ni = natal_names.index(np_)
        a_t = _lon_to_angle(transit_lons[ti])
        a_n = _lon_to_angle(natal_lons[ni])
        xt = transit_asp_r * math.cos(a_t); yt = transit_asp_r * math.sin(a_t)
        xn = asp_r * math.cos(a_n); yn = asp_r * math.sin(a_n)
        col = ASP_COLORS.get(asp_name, '#7f8c8d')
        alpha = max(0.3, 0.9 - orb_val * 0.12)
        ax.plot([xt, xn], [yt, yn], color=col, linewidth=1.8, alpha=alpha,
                linestyle='-.', zorder=5)

    # ── Натальні планети (золоті, зовнішнє кільце) ───────────────────────────
    natal_planet_r = 1.02
    # Розраховуємо офсети щоб не накладались
    sorted_natal = sorted(enumerate(natal_lons), key=lambda x: x[1])
    offsets = [0.0] * len(natal_lons)
    for k in range(1, len(sorted_natal)):
        prev_idx, prev_lon = sorted_natal[k-1]
        curr_idx, curr_lon = sorted_natal[k]
        if (curr_lon - prev_lon) % 360 < 6:
            offsets[curr_idx] = offsets[prev_idx] + 0.06

    for i, key in enumerate(natal_names):
        lon = natal_lons[i] + offsets[i] * 30
        angle = _lon_to_angle(lon)
        px = natal_planet_r * math.cos(angle)
        py = natal_planet_r * math.sin(angle)

        # Фон
        circle = plt.Circle((px, py), 0.055, color='#1a2530', zorder=10)
        ax.add_patch(circle)
        circle2 = plt.Circle((px, py), 0.055, color=NATAL_COLOR, fill=False, linewidth=1.5, zorder=11)
        ax.add_patch(circle2)

        sym = PLANET_SYMBOLS.get(key, key[:2])
        ax.text(px, py, sym, ha='center', va='center', fontsize=10,
                color=NATAL_COLOR, fontweight='bold', zorder=12,
                fontfamily='DejaVu Sans')

        # Лінія-покажчик до куспіду
        angle2 = _lon_to_angle(natal_lons[i])
        x_tick = 1.19 * math.cos(angle2)
        y_tick = 1.19 * math.sin(angle2)
        ax.plot([px, x_tick], [py, y_tick], color=NATAL_COLOR, linewidth=0.5, alpha=0.3, zorder=6)

    # ── Транзитні планети (сині, внутрішнє кільце) ───────────────────────────
    transit_planet_r = 0.88
    sorted_transit = sorted(enumerate(transit_lons), key=lambda x: x[1])
    t_offsets = [0.0] * len(transit_lons)
    for k in range(1, len(sorted_transit)):
        prev_idx, prev_lon = sorted_transit[k-1]
        curr_idx, curr_lon = sorted_transit[k]
        if (curr_lon - prev_lon) % 360 < 6:
            t_offsets[curr_idx] = t_offsets[prev_idx] + 0.07

    for i, key in enumerate(transit_names):
        lon = transit_lons[i] + t_offsets[i] * 30
        angle = _lon_to_angle(lon)
        px = transit_planet_r * math.cos(angle)
        py = transit_planet_r * math.sin(angle)

        # Фон
        circle = plt.Circle((px, py), 0.052, color='#0d1117', zorder=10)
        ax.add_patch(circle)
        circle2 = plt.Circle((px, py), 0.052, color=TRANSIT_COLOR, fill=False, linewidth=1.5, zorder=11)
        ax.add_patch(circle2)

        sym = PLANET_SYMBOLS.get(key, key[:2])
        ax.text(px, py, sym, ha='center', va='center', fontsize=9,
                color=TRANSIT_COLOR, fontweight='bold', zorder=12,
                fontfamily='DejaVu Sans')

    # ── Заголовок та легенда ─────────────────────────────────────────────────
    now_local = now_utc + timedelta(hours=2)
    title = f"Натальна карта + Транзити · {now_local.strftime('%d.%m.%Y %H:%M')}"
    ax.text(0, 1.47, title, ha='center', va='center', fontsize=11,
            color='#ecf0f1', fontweight='bold', zorder=15, fontfamily='DejaVu Sans')

    # Легенда аспектів
    legend_items = [
        ('──', ASP_COLORS['conjunction'], 'Конфліктація'),
        ('──', ASP_COLORS['trine'],       'Трин (тригон)'),
        ('──', ASP_COLORS['sextile'],     'Секстиль'),
        ('──', ASP_COLORS['square'],      'Квадратура'),
        ('──', ASP_COLORS['opposition'],  'Опозиція'),
    ]
    lx = -1.45
    for k, (sym, col, label) in enumerate(legend_items):
        ly = -1.20 + k * 0.12
        ax.plot([lx, lx+0.12], [ly, ly], color=col, linewidth=2.5, zorder=15)
        ax.text(lx+0.16, ly, label, va='center', fontsize=7, color='#bdc3c7', zorder=15,
                fontfamily='DejaVu Sans')

    # Легенда кільця планет
    ax.plot([0.7, 0.82], [-1.38, -1.38], color=NATAL_COLOR, linewidth=2, zorder=15)
    ax.text(0.86, -1.38, 'Натальні', va='center', fontsize=7, color=NATAL_COLOR,
            fontfamily='DejaVu Sans')
    ax.plot([0.7, 0.82], [-1.26, -1.26], color=TRANSIT_COLOR, linewidth=2, zorder=15)
    ax.text(0.86, -1.26, 'Транзити', va='center', fontsize=7, color=TRANSIT_COLOR,
            fontfamily='DejaVu Sans')

    # Дата народження
    ax.text(0, -1.48, 'Народження: 22.09.1989 · 02:52 · Львів', ha='center', va='center',
            fontsize=7, color='#7f8c8d', zorder=15, fontfamily='DejaVu Sans')

    plt.savefig(output_path, dpi=150, bbox_inches='tight',
                facecolor='#0d1117', edgecolor='none')
    plt.close(fig)
    return output_path


if __name__ == "__main__":
    path = generate_natal_chart("/tmp/test_chart.png")
    print(f"Chart saved: {path}")
