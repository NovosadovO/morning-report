"""
Графіки для Strava / біг-модуль.
Повертає bytes (PNG) для відправки через Telegram sendPhoto.

ДИЗАЙН: 🎨 Яскравий з емодзі + Moving Average тренд-лінії
АДАПТОВАНО: get_activities(days=N) — беремо днів від минулого
"""
import io
import os
from datetime import datetime, timedelta
import calendar

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_MPL = True
except Exception:
    HAS_MPL = False

# 🎨 ЯСКРАВИЙ ДИЗАЙН
BRIGHT_BG    = "#FFFFFF"      # Білий фон
BRIGHT_CARD  = "#F5F7FA"      # Світло-сіра панель
BRIGHT_GREEN = "#00D084"      # Яскравий зелений ✅
BRIGHT_BLUE  = "#0080FF"      # Яскравий синій 💙
BRIGHT_ORANGE = "#FF9500"     # Яскравий помаранчевий 🔥
BRIGHT_PINK  = "#FF006E"      # Яскравий рожевий 💪
TREND_LINE   = "#FF6B35"      # Помаранчева лінія тренду
TEXT_DARK    = "#333333"      # Темний текст
TEXT_MUTED   = "#666666"      # Muted текст

# DEBUG
import sys as _debug_sys
print(f"[STRAVA_CHARTS] LOADED (BRIGHT DESIGN)", file=_debug_sys.stderr, flush=True)


def _moving_average(data, window=7):
    """Ковзаюча середня з numpy"""
    if not HAS_NUMPY or np is None:
        return None
    try:
        if len(data) < window:
            return data
        ma = np.convolve(data, np.ones(window)/window, mode='valid')
        # Pad з NaN на початку
        return np.concatenate([np.full(window-1, np.nan), ma])
    except:
        return None


def _setup_bright_style():
    """Налаштування яскравого стилю"""
    if not HAS_MPL:
        return
    plt.rcParams.update({
        'figure.facecolor': BRIGHT_BG,
        'axes.facecolor': BRIGHT_CARD,
        'axes.edgecolor': '#CCCCCC',
        'text.color': TEXT_DARK,
        'axes.labelcolor': TEXT_DARK,
        'xtick.color': TEXT_MUTED,
        'ytick.color': TEXT_MUTED,
        'grid.color': '#EEEEEE',
        'grid.alpha': 0.3,
        'font.size': 11,
        'font.family': 'sans-serif',
    })


def plot_week_chart(weeks_back=8):
    """
    Тижневий прогрес за останні N тижнів (N*7 днів).
    Повертає PNG bytes або None.
    """
    if not HAS_MPL:
        return None
    
    try:
        from strava import get_activities
        
        # Беремо діяльність за weeks_back * 7 днів
        days_back = weeks_back * 7
        activities = get_activities(days=days_back)
        if not activities:
            return None
        
        # Групуємо по дням
        daily_km = {}
        for act in activities:
            if 'start_date' not in act:
                continue
            
            # Парсимо дату
            start_date_str = act['start_date']
            if isinstance(start_date_str, str):
                date_str = start_date_str[:10]  # YYYY-MM-DD
            else:
                date_str = str(start_date_str.date()) if hasattr(start_date_str, 'date') else str(start_date_str)[:10]
            
            km = act.get('distance', 0) / 1000  # meters -> km
            if date_str not in daily_km:
                daily_km[date_str] = 0
            daily_km[date_str] += km
        
        if not daily_km:
            return None
        
        # Сортуємо по датам
        sorted_dates = sorted(daily_km.keys())
        km_values = np.array([daily_km[d] for d in sorted_dates]) if HAS_NUMPY else [daily_km[d] for d in sorted_dates]
        
        # Побудова графіка
        _setup_bright_style()
        fig, ax = plt.subplots(figsize=(14, 8), facecolor=BRIGHT_BG)
        
        # Стовпці
        ax.bar(range(len(km_values)), km_values, color=BRIGHT_BLUE, alpha=0.7, label="км/день", edgecolor=BRIGHT_BLUE, linewidth=1)
        
        # MA 7-day тренд
        if HAS_NUMPY:
            ma = _moving_average(km_values, window=7)
            if ma is not None:
                ax.plot(range(len(ma)), ma, color=TREND_LINE, linewidth=3, label="7-day Moving Avg", zorder=10)
        
        # Оформлення
        ax.set_xlabel("Дата", fontsize=12, color=TEXT_DARK, weight='bold')
        ax.set_ylabel("км", fontsize=12, color=TEXT_DARK, weight='bold')
        ax.set_title(f"📊 Біг — Тижневий Прогрес ({weeks_back}w)", fontsize=16, color=TEXT_DARK, weight='bold', pad=20)
        
        # Часові мітки
        tick_step = max(1, len(sorted_dates) // 10)
        ax.set_xticks(range(0, len(sorted_dates), tick_step))
        ax.set_xticklabels([sorted_dates[i] for i in range(0, len(sorted_dates), tick_step)], rotation=45, ha='right')
        
        ax.grid(True, alpha=0.3, linestyle='--', axis='y')
        ax.legend(loc='upper left', fontsize=10, framealpha=0.95)
        ax.set_facecolor(BRIGHT_CARD)
        
        # Збереження в bytes
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor=BRIGHT_BG)
        buf.seek(0)
        plt.close(fig)
        return buf.getvalue()
        
    except Exception as e:
        print(f"[STRAVA_CHARTS] plot_week_chart error: {e}", file=_debug_sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=_debug_sys.stderr)
        return None


def plot_month_chart(year=None, month=None):
    """
    Місячний графік (День → км).
    Якщо year/month не задані, використовує попередній місяць.
    Повертає PNG bytes або None.
    """
    if not HAS_MPL:
        return None
    
    try:
        from strava import get_activities
        
        # Визначаємо період (поточний або заданий місяць)
        now = datetime.now()
        if year is None or month is None:
            if now.month == 1:
                year, month = now.year - 1, 12
            else:
                year, month = now.year, now.month - 1
        
        # Дати початку й кінця місяця
        first_day = datetime(year, month, 1)
        if month == 12:
            last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = datetime(year, month + 1, 1) - timedelta(days=1)
        
        days_in_month = (last_day - first_day).days + 1
        
        # Беремо діяльність за останні 90 днів (безпека)
        activities = get_activities(days=90)
        if not activities:
            return None
        
        # Групуємо по дням ЦЬОГО місяця
        daily_km = {}
        for day_num in range(1, days_in_month + 1):
            daily_km[day_num] = 0.0
        
        for act in activities:
            if 'start_date' not in act:
                continue
            
            # Парсимо дату
            start_date_str = act['start_date']
            if isinstance(start_date_str, str):
                act_date = datetime.fromisoformat(start_date_str[:10])
            else:
                act_date = start_date_str if isinstance(start_date_str, datetime) else datetime.fromisoformat(str(start_date_str)[:10])
            
            # Фільтруємо по місяцю
            if act_date.year == year and act_date.month == month:
                day_num = act_date.day
                km = act.get('distance', 0) / 1000
                daily_km[day_num] += km
        
        km_values = np.array([daily_km[d] for d in range(1, days_in_month + 1)]) if HAS_NUMPY else [daily_km[d] for d in range(1, days_in_month + 1)]
        
        if not HAS_NUMPY:
            km_values = np.array(km_values)
        
        # Побудова графіка
        _setup_bright_style()
        fig, ax = plt.subplots(figsize=(14, 8), facecolor=BRIGHT_BG)
        
        # Стовпці
        ax.bar(range(1, days_in_month + 1), km_values, color=BRIGHT_GREEN, alpha=0.8, label="км/день", edgecolor=BRIGHT_GREEN, linewidth=1)
        
        # MA 7-day тренд
        if HAS_NUMPY:
            ma = _moving_average(km_values, window=7)
            if ma is not None:
                ax.plot(range(len(ma)), ma, color=TREND_LINE, linewidth=3, label="7-day Moving Avg", zorder=10)
        
        # Оформлення
        mnames = ["","Січень","Лютий","Березень","Квітень","Травень","Червень",
                  "Липень","Серпень","Вересень","Жовтень","Листопад","Грудень"]
        ax.set_xlabel("День місяця", fontsize=12, color=TEXT_DARK, weight='bold')
        ax.set_ylabel("км", fontsize=12, color=TEXT_DARK, weight='bold')
        ax.set_title(f"📊 {mnames[month]} {year}", fontsize=16, color=TEXT_DARK, weight='bold', pad=20)
        
        ax.set_xticks(range(1, days_in_month + 1, max(1, days_in_month // 10)))
        ax.grid(True, alpha=0.3, linestyle='--', axis='y')
        ax.legend(loc='upper left', fontsize=10, framealpha=0.95)
        ax.set_facecolor(BRIGHT_CARD)
        
        # Збереження в bytes
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor=BRIGHT_BG)
        buf.seek(0)
        plt.close(fig)
        return buf.getvalue()
        
    except Exception as e:
        print(f"[STRAVA_CHARTS] plot_month_chart error: {e}", file=_debug_sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=_debug_sys.stderr)
        return None


def plot_year_chart(year=None):
    """
    Річний графік (місяц → км).
    Якщо year не задан, використовує поточний рік.
    Повертає PNG bytes або None.
    """
    if not HAS_MPL:
        return None
    
    try:
        from strava import get_activities
        
        if year is None:
            year = datetime.now().year
        
        # Беремо діяльність за останні 400 днів (1+ року)
        activities = get_activities(days=400)
        if not activities:
            return None
        
        # Групуємо по місяцях ЦЬОГО року
        monthly_km = {}
        for month_num in range(1, 13):
            monthly_km[month_num] = 0.0
        
        for act in activities:
            if 'start_date' not in act:
                continue
            
            # Парсимо дату
            start_date_str = act['start_date']
            if isinstance(start_date_str, str):
                act_date = datetime.fromisoformat(start_date_str[:10])
            else:
                act_date = start_date_str if isinstance(start_date_str, datetime) else datetime.fromisoformat(str(start_date_str)[:10])
            
            # Фільтруємо по року
            if act_date.year == year:
                month_num = act_date.month
                km = act.get('distance', 0) / 1000
                monthly_km[month_num] += km
        
        km_values = np.array([monthly_km[m] for m in range(1, 13)]) if HAS_NUMPY else [monthly_km[m] for m in range(1, 13)]
        
        if not HAS_NUMPY:
            km_values = np.array(km_values)
        
        # Побудова графіка
        _setup_bright_style()
        fig, ax = plt.subplots(figsize=(15, 8), facecolor=BRIGHT_BG)
        
        # Стовпці
        ax.bar(range(1, 13), km_values, color=BRIGHT_ORANGE, alpha=0.8, label="км/місяць", edgecolor=BRIGHT_ORANGE, linewidth=1.5)
        
        # Оформлення
        mnames = ["Січ","Лют","Бер","Кві","Тра","Чер","Лип","Сер","Вер","Жов","Лис","Гру"]
        ax.set_xlabel("Місяць", fontsize=12, color=TEXT_DARK, weight='bold')
        ax.set_ylabel("км", fontsize=12, color=TEXT_DARK, weight='bold')
        ax.set_title(f"📊 Біг — Річний Звіт {year}", fontsize=16, color=TEXT_DARK, weight='bold', pad=20)
        
        ax.set_xticks(range(1, 13))
        ax.set_xticklabels(mnames, rotation=0)
        ax.grid(True, alpha=0.3, linestyle='--', axis='y')
        ax.legend(loc='upper left', fontsize=10, framealpha=0.95)
        ax.set_facecolor(BRIGHT_CARD)
        
        # Збереження в bytes
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor=BRIGHT_BG)
        buf.seek(0)
        plt.close(fig)
        return buf.getvalue()
        
    except Exception as e:
        print(f"[STRAVA_CHARTS] plot_year_chart error: {e}", file=_debug_sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=_debug_sys.stderr)
        return None
