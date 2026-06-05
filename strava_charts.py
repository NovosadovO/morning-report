"""
Графіки для Strava / біг-модуль.
Повертає bytes (PNG) для відправки через Telegram sendPhoto.
"""
import io
import os
from datetime import datetime, timedelta
import calendar

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.patches import FancyBboxPatch
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# Стиль — темна тема
DARK_BG    = "#0d1117"
CARD_BG    = "#161b22"
ACCENT     = "#3FB950"   # зелений
ACCENT2    = "#58a6ff"   # синій
ORANGE     = "#f0883e"
RED        = "#f85149"
TEXT_COLOR = "#e6edf3"
MUTED      = "#8b949e"


def _setup_dark_style():
    plt.rcParams.update({
        "figure.facecolor":  DARK_BG,
        "axes.facecolor":    CARD_BG,
        "axes.edgecolor":    MUTED,
        "axes.labelcolor":   TEXT_COLOR,
        "axes.titlecolor":   TEXT_COLOR,
        "xtick.color":       MUTED,
        "ytick.color":       MUTED,
        "text.color":        TEXT_COLOR,
        "grid.color":        "#21262d",
        "grid.linewidth":    0.8,
        "font.size":         9,
        "axes.titlesize":    11,
    })


def plot_month_chart(year: int = None, month: int = None) -> bytes:
    """
    Місячний графік:
    - Bar chart: км по днях
    - Line overlay: темп (хв/км)
    Повертає bytes PNG.
    """
    if not HAS_MPL:
        return b""

    from strava import get_month_stats

    now = datetime.now()
    if year is None: year = now.year
    if month is None: month = now.month

    ms = get_month_stats(year, month)
    runs = ms.get("runs_list", [])

    # Будуємо дані по днях місяця
    days_in_month = calendar.monthrange(year, month)[1]
    days = list(range(1, days_in_month + 1))
    km_by_day    = {d: 0.0 for d in days}
    pace_by_day  = {d: None for d in days}

    for r in runs:
        d = r["date"].day
        km_by_day[d]   += r["dist_km"]
        # середній темп якщо кілька пробіжок в день
        if r["pace_sec"] > 0:
            pace_by_day[d] = r["pace_sec"]

    km_vals   = [km_by_day[d] for d in days]
    pace_vals = [pace_by_day[d] for d in days]

    month_names = ["", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
                   "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]

    _setup_dark_style()
    fig, ax1 = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor(DARK_BG)

    # Bars
    colors = [ACCENT if k > 0 else "#21262d" for k in km_vals]
    bars = ax1.bar(days, km_vals, color=colors, width=0.7, zorder=2)

    # Підпис на барах > 0
    for bar, km in zip(bars, km_vals):
        if km > 0:
            ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                     f"{km:.1f}", ha="center", va="bottom", fontsize=7,
                     color=TEXT_COLOR, fontweight="bold")

    ax1.set_xlabel("День місяця", color=MUTED, fontsize=9)
    ax1.set_ylabel("Км", color=ACCENT, fontsize=9)
    ax1.set_xlim(0.5, days_in_month + 0.5)
    ax1.set_xticks(days)
    ax1.tick_params(axis="x", labelsize=7)
    ax1.grid(axis="y", zorder=0)

    # Темп — права вісь
    pace_exists = [p for p in pace_vals if p is not None]
    if pace_exists:
        ax2 = ax1.twinx()
        ax2.set_facecolor(CARD_BG)
        xs = [d for d, p in zip(days, pace_vals) if p is not None]
        ys = [p / 60 for d, p in zip(days, pace_vals) if p is not None]  # в хвилинах
        ax2.plot(xs, ys, color=ACCENT2, linewidth=1.5, marker="o", markersize=4, zorder=3, label="темп")
        ax2.set_ylabel("Темп хв/км", color=ACCENT2, fontsize=9)
        ax2.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: f"{int(v)}:{int((v%1)*60):02d}")
        )
        ax2.invert_yaxis()  # менший = швидший = вгорі
        ax2.tick_params(axis="y", colors=ACCENT2, labelsize=8)

    # Заголовок + статистика
    total_km = ms["km"]
    runs_count = ms["runs"]
    title = f"🏃 {month_names[month]} {year}  ·  {runs_count} пробіжок  ·  {total_km} км"
    if ms.get("avg_pace_str") and ms["avg_pace_str"] != "—":
        title += f"  ·  ∅ {ms['avg_pace_str']} хв/км"
    ax1.set_title(title, color=TEXT_COLOR, fontsize=10, pad=10)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=DARK_BG)
    plt.close()
    buf.seek(0)
    return buf.read()


def plot_year_chart(year: int = None) -> bytes:
    """
    Річний графік:
    - Bar: км по місяцях
    - Line: кількість пробіжок
    Повертає bytes PNG.
    """
    if not HAS_MPL:
        return b""

    from strava import get_year_stats

    now = datetime.now()
    if year is None: year = now.year

    ys = get_year_stats(year)
    monthly = ys.get("monthly", {})

    months = list(range(1, 13))
    month_labels = ["Січ","Лют","Бер","Кві","Тра","Чер","Лип","Сер","Вер","Жов","Лис","Гру"]
    km_vals   = [monthly.get(m, {}).get("km", 0) for m in months]
    run_vals  = [monthly.get(m, {}).get("runs", 0) for m in months]

    # Не показуємо майбутні місяці
    current_month = now.month if now.year == year else 12
    km_vals  = km_vals[:current_month]
    run_vals = run_vals[:current_month]
    labels   = month_labels[:current_month]
    x        = list(range(len(labels)))

    _setup_dark_style()
    fig, ax1 = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor(DARK_BG)

    # Cumulative line
    cumulative = []
    cum = 0
    for k in km_vals:
        cum += k
        cumulative.append(round(cum, 1))

    # Bars
    bars = ax1.bar(x, km_vals, color=ACCENT, width=0.5, zorder=2, label="км/місяць")
    for bar, km in zip(bars, km_vals):
        if km > 0:
            ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                     f"{km:.0f}", ha="center", va="bottom", fontsize=8,
                     color=TEXT_COLOR, fontweight="bold")

    ax1.set_ylabel("Км / місяць", color=ACCENT, fontsize=9)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, fontsize=9)
    ax1.grid(axis="y", zorder=0)

    # Cumulative line — права вісь
    ax2 = ax1.twinx()
    ax2.set_facecolor(CARD_BG)
    ax2.plot(x, cumulative, color=ORANGE, linewidth=2, marker="o", markersize=5,
             zorder=3, label="накопичено")
    for i, (xi, cv) in enumerate(zip(x, cumulative)):
        ax2.annotate(f"{cv:.0f}", (xi, cv), textcoords="offset points",
                     xytext=(0, 6), ha="center", fontsize=7, color=ORANGE)
    ax2.set_ylabel("Накопичено км", color=ORANGE, fontsize=9)
    ax2.tick_params(axis="y", colors=ORANGE, labelsize=8)

    total_km    = ys["km"]
    total_runs  = ys["runs"]
    ax1.set_title(
        f"🏃 {year} рік  ·  {total_runs} пробіжок  ·  {total_km} км загалом",
        color=TEXT_COLOR, fontsize=10, pad=10
    )

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=DARK_BG)
    plt.close()
    buf.seek(0)
    return buf.read()


def plot_week_chart(weeks_back: int = 8) -> bytes:
    """
    Тижневий прогрес — останні N тижнів.
    Bar: км по тижнях + marker для пробіжок.
    """
    if not HAS_MPL:
        return b""

    from strava import get_runs

    runs = get_runs(days=weeks_back * 7 + 3)

    now = datetime.now()
    week_start_of_current = now - timedelta(days=now.weekday())
    week_start_of_current = week_start_of_current.replace(hour=0, minute=0, second=0, microsecond=0)

    weeks = []
    for i in range(weeks_back - 1, -1, -1):
        ws = week_start_of_current - timedelta(weeks=i)
        we = ws + timedelta(days=7)
        wk_runs = [r for r in runs if ws <= r["date"] < we]
        km = round(sum(r["dist_km"] for r in wk_runs), 1)
        count = len(wk_runs)
        label = ws.strftime("%d.%m")
        weeks.append({"label": label, "km": km, "runs": count})

    labels  = [w["label"] for w in weeks]
    km_vals = [w["km"] for w in weeks]
    run_cnt = [w["runs"] for w in weeks]
    x = list(range(len(weeks)))

    _setup_dark_style()
    fig, ax1 = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor(DARK_BG)

    # Поточний тиждень виділяємо
    colors = [ACCENT2 if i == len(weeks) - 1 else ACCENT for i in x]
    bars = ax1.bar(x, km_vals, color=colors, width=0.6, zorder=2)

    for bar, km, cnt in zip(bars, km_vals, run_cnt):
        if km > 0:
            ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                     f"{km}", ha="center", va="bottom", fontsize=12,
                     color=TEXT_COLOR, fontweight="bold")
            ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() / 2,
                     f"×{cnt}", ha="center", va="center", fontsize=10, color=DARK_BG)

    ax1.set_ylabel("Км / тиждень", color=ACCENT, fontsize=12)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, fontsize=11)
    ax1.tick_params(colors=TEXT_COLOR, labelsize=11)
    ax1.grid(axis="y", zorder=0)

    total_km = sum(km_vals)
    ax1.set_title(
        f"🏃 Останні {weeks_back} тижнів  ·  {total_km:.1f} км",
        color=TEXT_COLOR, fontsize=13, pad=12
    )

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=DARK_BG)
    plt.close()
    buf.seek(0)
    return buf.read()


if __name__ == "__main__":
    # Тест — зберігаємо локально
    print("Генерую місячний графік...")
    data = plot_month_chart()
    if data:
        with open("/tmp/test_month.png", "wb") as f:
            f.write(data)
        print(f"Saved {len(data)//1024}KB → /tmp/test_month.png")

    print("Генерую річний графік...")
    data2 = plot_year_chart()
    if data2:
        with open("/tmp/test_year.png", "wb") as f:
            f.write(data2)
        print(f"Saved {len(data2)//1024}KB → /tmp/test_year.png")
