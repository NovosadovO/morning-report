"""
Графіки для Strava / біг-модуль.
Повертає bytes (PNG) для відправки через Telegram sendPhoto.

ДИЗАЙН: 🎨 Яскравий з емодзі + Moving Average тренд-лінії + 1 місяць
"""
import io
import os
from datetime import datetime, timedelta
import calendar
import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.patches import FancyBboxPatch
    HAS_MPL = True
except Exception:
    HAS_MPL = False

# 🎨 ЯСКРАВИЙ ДИЗАЙН з ЕМОДЗІ
BRIGHT_BG    = "#FFFFFF"      # Білий фон
BRIGHT_CARD  = "#F5F7FA"      # Світло-сіра панель
BRIGHT_GREEN = "#00D084"      # Яскравий зелений ✅
BRIGHT_BLUE  = "#0080FF"      # Яскравий синій 💙
BRIGHT_ORANGE = "#FF9500"     # Яскравий помаранчевий 🔥
BRIGHT_PINK  = "#FF006E"      # Яскравий рожевий 💪
TREND_LINE   = "#FF6B35"      # Помаранчева лінія тренду
TEXT_DARK    = "#333333"      # Темний текст
TEXT_MUTED   = "#666666"      # Мuted текст

# DEBUG
import sys as _debug_sys
print(f"[STRAVA_CHARTS] LOADED (BRIGHT DESIGN)", file=_debug_sys.stderr, flush=True)


def _moving_average(data, window=7):
    """Ковзаюча середня"""
    if len(data) < window:
        return data
    ma = np.convolve(data, np.ones(window)/window, mode='valid')
    # Pad з нулями на початку
    return np.concatenate([np.full(window-1, np.nan), ma])


def _setup_bright_style():
    """Налаштування яскравого стилю"""
    plt.rcParams.update({
        "figure.facecolor":  BRIGHT_BG,
        "axes.facecolor":    BRIGHT_CARD,
        "axes.edgecolor":    "#CCCCCC",
        "axes.labelcolor":   TEXT_DARK,
        "axes.titlecolor":   TEXT_DARK,
        "xtick.color":       TEXT_MUTED,
        "ytick.color":       TEXT_MUTED,
        "text.color":        TEXT_DARK,
        "grid.color":        "#E0E0E0",
        "grid.linewidth":    0.6,
        "font.size":         10,
        "axes.titlesize":    13,
        "legend.framealpha": 0.95,
    })


def _buf(fig, dpi: int = 200) -> bytes:
    """Рендер фігури у bytes PNG"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor=BRIGHT_BG)
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue()


def plot_month_chart(year: int = None, month: int = None) -> bytes:
    """
    🏃 Місячний графік Strava (1 місяць):
    - 📊 Bar chart: км по днях (яскравий зелений)
    - 📈 Line overlay: Moving Average (тренд помаранчевий)
    - 🎯 Загальний тренд за місяць
    """
    if not HAS_MPL:
        return b""

    from strava import get_month_stats

    now = datetime.now()
    if year is None: year = now.year
    if month is None: month = now.month

    ms = get_month_stats(year, month)
    runs = ms.get("runs_list", [])

    if not runs:
        return None

    # Будуємо дані по днях
    days_in_month = calendar.monthrange(year, month)[1]
    days = list(range(1, days_in_month + 1))
    km_by_day = {d: 0.0 for d in days}
    pace_by_day = {d: None for d in days}

    for r in runs:
        d = r["date"].day
        km_by_day[d] += r["dist_km"]
        if r["pace_sec"] > 0:
            pace_by_day[d] = r["pace_sec"]

    km_vals = np.array([km_by_day[d] for d in days])
    pace_vals = [pace_by_day[d] for d in days]

    month_names = ["", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
                   "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]
    
    total_km = ms.get("total_km", 0)
    total_runs = ms.get("total_runs", 0)
    avg_pace = ms.get("avg_pace_sec", 0)

    _setup_bright_style()
    fig = plt.figure(figsize=(14, 8))
    
    # Заголовок з емодзі
    fig.suptitle(f"🏃 {month_names[month]} {year} • {total_runs} пробіжок • {total_km:.1f} км", 
                 fontsize=16, fontweight='bold', color=TEXT_DARK, y=0.98)

    # Основний графік
    ax1 = plt.subplot(111)
    
    # Bars (км по днях)
    colors = [BRIGHT_GREEN if k > 0 else BRIGHT_CARD for k in km_vals]
    bars = ax1.bar(days, km_vals, color=colors, alpha=0.85, label="Км за день", edgecolor=BRIGHT_GREEN, linewidth=1.5)
    
    # Moving Average (7-day)
    ma_7 = _moving_average(km_vals, window=7)
    ax1.plot(days, ma_7, color=TREND_LINE, linewidth=3, label="📈 Тренд (7-day MA)", marker='o', markersize=5, alpha=0.9)
    
    ax1.set_xlabel("День місяця", fontsize=11, color=TEXT_DARK, fontweight='bold')
    ax1.set_ylabel("Км 🏃", fontsize=11, color=TEXT_DARK, fontweight='bold')
    ax1.set_xticks(days[::2])  # Кожен другий день
    ax1.legend(loc='upper left', fontsize=10, framealpha=0.95)
    ax1.grid(True, alpha=0.3, linestyle='--')
    ax1.set_ylim(bottom=0)

    plt.tight_layout()
    return _buf(fig, dpi=200)


def plot_week_chart(weeks_back: int = 4) -> bytes:
    """
    📅 Тижневий графік (останні 4 тижні = 1 місяць):
    - Км по днях тижня
    - Moving Average
    - Статистика
    """
    if not HAS_MPL:
        return b""

    from strava import get_week_stats, get_activities

    # Беремо останні 30 днів
    today = datetime.now().date()
    start_date = today - timedelta(days=30)
    
    activities = get_activities(start_date=start_date, end_date=today)
    
    if not activities:
        return None

    # Сортуємо по датам
    activities.sort(key=lambda x: x["start_date"])
    
    # Будуємо дані по днях (за останні 30)
    dates = []
    km_vals = []
    current_date = start_date
    daily_km = {}
    
    while current_date <= today:
        daily_km[current_date] = 0.0
        current_date += timedelta(days=1)
    
    for act in activities:
        act_date = act["start_date"].date() if hasattr(act["start_date"], 'date') else act["start_date"]
        if act_date in daily_km:
            daily_km[act_date] += act.get("distance", 0) / 1000  # meters to km
    
    for d in sorted(daily_km.keys()):
        dates.append(d)
        km_vals.append(daily_km[d])
    
    km_vals = np.array(km_vals)
    total_km = km_vals.sum()
    total_runs = len(activities)

    _setup_bright_style()
    fig = plt.figure(figsize=(14, 8))
    
    # Заголовок
    fig.suptitle(f"📅 Останні 30 днів • {total_runs} пробіжок • {total_km:.1f} км", 
                 fontsize=16, fontweight='bold', color=TEXT_DARK, y=0.98)

    ax = plt.subplot(111)
    
    # Bars (яскравий синій)
    colors = [BRIGHT_BLUE if k > 0 else BRIGHT_CARD for k in km_vals]
    ax.bar(range(len(km_vals)), km_vals, color=colors, alpha=0.85, label="Км", edgecolor=BRIGHT_BLUE, linewidth=1.5)
    
    # Moving Average (7-day)
    ma_7 = _moving_average(km_vals, window=7)
    ax.plot(range(len(km_vals)), ma_7, color=TREND_LINE, linewidth=3, label="📈 Тренд (7-day MA)", 
            marker='o', markersize=5, alpha=0.9)
    
    # X-axis (дати)
    date_strs = [d.strftime("%d.%m") for d in dates]
    ax.set_xticks(range(0, len(dates), 5))
    ax.set_xticklabels([date_strs[i] for i in range(0, len(dates), 5)], fontsize=9)
    
    ax.set_xlabel("Дата", fontsize=11, color=TEXT_DARK, fontweight='bold')
    ax.set_ylabel("Км 🏃", fontsize=11, color=TEXT_DARK, fontweight='bold')
    ax.legend(loc='upper left', fontsize=10, framealpha=0.95)
    ax.grid(True, alpha=0.3, linestyle='--')
    ax.set_ylim(bottom=0)

    plt.tight_layout()
    return _buf(fig, dpi=200)


def plot_year_chart(year: int = None) -> bytes:
    """
    📊 Річний графік (12 місяців):
    - Км по місяцях
    - Moving Average
    """
    if not HAS_MPL:
        return b""

    from strava import get_year_stats

    if year is None:
        year = datetime.now().year

    ys = get_year_stats(year)
    months = list(range(1, 13))
    km_by_month = [ys.get(m, {}).get("total_km", 0) for m in months]
    km_vals = np.array(km_by_month)

    month_names = ["Січ", "Лют", "Бер", "Кві", "Тра", "Чер", "Лип", "Сер", "Вер", "Жов", "Лис", "Гру"]
    total_km = km_vals.sum()

    _setup_bright_style()
    fig = plt.figure(figsize=(14, 8))
    
    fig.suptitle(f"📊 {year} рік • {total_km:.1f} км", 
                 fontsize=16, fontweight='bold', color=TEXT_DARK, y=0.98)

    ax = plt.subplot(111)
    
    # Bars (помаранчевий)
    colors = [BRIGHT_ORANGE if k > 0 else BRIGHT_CARD for k in km_vals]
    ax.bar(months, km_vals, color=colors, alpha=0.85, label="Км", edgecolor=BRIGHT_ORANGE, linewidth=1.5, width=0.7)
    
    # Moving Average (3-month)
    ma_3 = _moving_average(km_vals, window=3)
    ax.plot(months, ma_3, color=TREND_LINE, linewidth=3, label="📈 Тренд (3-month MA)", 
            marker='o', markersize=6, alpha=0.9)
    
    ax.set_xticks(months)
    ax.set_xticklabels(month_names, fontsize=10)
    ax.set_xlabel("Місяць", fontsize=11, color=TEXT_DARK, fontweight='bold')
    ax.set_ylabel("Км 🏃", fontsize=11, color=TEXT_DARK, fontweight='bold')
    ax.legend(loc='upper left', fontsize=10, framealpha=0.95)
    ax.grid(True, alpha=0.3, linestyle='--', axis='y')
    ax.set_ylim(bottom=0)

    plt.tight_layout()
    return _buf(fig, dpi=200)


if __name__ == "__main__":
    print("✅ strava_charts.py loaded")
