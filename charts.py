"""
charts.py — Комбіновані графіки для підсумків дня / тижня / місяця.
Темна тема, стиль GitHub/Strava.
"""
import io
import os
from datetime import datetime, timedelta, date, timezone

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.gridspec as gridspec
    import numpy as np
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ── Палітра ───────────────────────────────────────────────────────────────────
BG      = "#0D1117"
PANEL   = "#161B22"
BORDER  = "#30363D"
TEXT    = "#E6EDF3"
MUTED   = "#8B949E"
GREEN   = "#3FB950"
BLUE    = "#58A6FF"
ORANGE  = "#F0883E"
RED     = "#F85149"
PURPLE  = "#A371F7"
YELLOW  = "#D29922"
GRID_C  = "#21262D"

HABIT_COLORS = {
    "shower": BLUE,
    "run":    GREEN,
    "water":  "#1F6FEB",
    "tea":    YELLOW,
    "sauna":  RED,
}
HABIT_LABELS = {
    "shower": "🚿 Душ",
    "run":    "🏃 Біг",
    "water":  "💧 Вода",
    "tea":    "🍵 Чай",
    "sauna":  "🧖 Сауна",
}

_DIR = os.path.dirname(os.path.abspath(__file__))


def _rc():
    plt.rcParams.update({
        "figure.facecolor": BG,
        "axes.facecolor":   PANEL,
        "axes.edgecolor":   BORDER,
        "axes.labelcolor":  TEXT,
        "axes.titlecolor":  TEXT,
        "xtick.color":      MUTED,
        "ytick.color":      MUTED,
        "text.color":       TEXT,
        "grid.color":       GRID_C,
        "grid.linewidth":   0.7,
        "font.size":        9,
        "axes.titlesize":   11,
        "axes.titlepad":    10,
    })


def _buf(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _load_habits():
    try:
        import sys; sys.path.insert(0, _DIR)
        from storage import load_habits
        return load_habits() or {}
    except Exception:
        return {}


def _load_weight():
    try:
        import sys; sys.path.insert(0, _DIR)
        import storage as _st
        data = _st.load("weight_data.json") or {}
        if not data:
            data = _st.load_weight() or {}
        return data
    except Exception:
        return {}


# ── 1. HEATMAP ЗВИЧОК (GitHub-style) ─────────────────────────────────────────

def plot_habits_heatmap(days: int = 30) -> bytes | None:
    """
    GitHub-style heatmap звичок за останні N днів.
    Кожна звичка — рядок, кожен день — клітинка.
    """
    if not HAS_MPL:
        return None
    try:
        _rc()
        raw = _load_habits()
        if not raw:
            return None

        today = date.today()
        all_dates = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
        HABITS = ["shower", "run", "water", "tea", "sauna"]

        # Матриця: [habit_idx][day_idx] = 0/1/nan
        matrix = []
        for hkey in HABITS:
            row = []
            for d in all_dates:
                entry = raw.get(d.isoformat())
                if entry is None:
                    row.append(np.nan)
                else:
                    v = entry.get(hkey)
                    row.append(1.0 if v is True else (0.0 if v is False else np.nan))
            matrix.append(row)
        matrix = np.array(matrix, dtype=float)

        n_habits = len(HABITS)
        fig_w = max(12, days * 0.38)
        fig, ax = plt.subplots(figsize=(fig_w, n_habits * 0.72 + 1.2), facecolor=BG)
        ax.set_facecolor(BG)
        ax.set_xlim(-0.5, days - 0.5)
        ax.set_ylim(-0.5, n_habits - 0.5)
        ax.set_yticks(range(n_habits))
        ax.set_yticklabels([HABIT_LABELS[h] for h in HABITS], fontsize=10, color=TEXT)
        ax.invert_yaxis()

        # Клітинки
        for hi, hkey in enumerate(HABITS):
            color = HABIT_COLORS[hkey]
            for di, d in enumerate(all_dates):
                val = matrix[hi, di]
                if np.isnan(val):
                    fc = "#1C2128"
                elif val == 1:
                    fc = color
                else:
                    fc = "#21262D"
                rect = mpatches.FancyBboxPatch(
                    (di - 0.42, hi - 0.42), 0.84, 0.84,
                    boxstyle="round,pad=0.04",
                    linewidth=0,
                    facecolor=fc,
                    alpha=0.9 if val == 1 else 1.0
                )
                ax.add_patch(rect)

        # X-axis: показуємо кожні 5 днів + перший/останній
        tick_positions = list(range(0, days, 5))
        if (days - 1) not in tick_positions:
            tick_positions.append(days - 1)
        ax.set_xticks(tick_positions)
        ax.set_xticklabels(
            [all_dates[i].strftime("%d.%m") for i in tick_positions],
            fontsize=8, color=MUTED
        )

        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(length=0)
        ax.grid(False)

        # Легенда
        legend_items = [
            mpatches.Patch(facecolor=GREEN, label="✓ Виконано"),
            mpatches.Patch(facecolor="#21262D", label="✗ Пропущено"),
            mpatches.Patch(facecolor="#1C2128", label="— Немає даних"),
        ]
        ax.legend(handles=legend_items, loc="upper right",
                  fontsize=8, framealpha=0.3,
                  facecolor=PANEL, edgecolor=BORDER,
                  labelcolor=TEXT)

        ax.set_title(f"📋 Звички — останні {days} днів", fontsize=12,
                     color=TEXT, pad=12, fontweight="bold")

        fig.tight_layout(pad=1.2)
        return _buf(fig)
    except Exception as e:
        print(f"[charts] habits_heatmap error: {e}")
        return None


# ── 2. ДАШБОРД ДНЯ ────────────────────────────────────────────────────────────

def plot_day_dashboard(today_str: str = None) -> bytes | None:
    """
    Дашборд одного дня: звички + вага (міні).
    Відправляється з підсумком дня (21:00).
    """
    if not HAS_MPL:
        return None
    try:
        _rc()
        raw = _load_habits()
        wdata = _load_weight()

        if today_str is None:
            today_str = date.today().isoformat()

        HABITS = ["shower", "run", "water", "tea", "sauna"]
        entry = raw.get(today_str, {}) or {}

        values = []
        colors = []
        labels = []
        for hkey in HABITS:
            v = entry.get(hkey)
            labels.append(HABIT_LABELS[hkey])
            if v is True:
                values.append(1)
                colors.append(HABIT_COLORS[hkey])
            else:
                values.append(0.15)
                colors.append("#21262D")

        fig, axes = plt.subplots(1, 2, figsize=(10, 4),
                                 gridspec_kw={"width_ratios": [2, 1]},
                                 facecolor=BG)

        # Ліво: горизонтальні бари звичок
        ax_h = axes[0]
        ax_h.set_facecolor(PANEL)
        y_pos = range(len(HABITS))
        bars = ax_h.barh(list(y_pos), values, height=0.6,
                         color=colors, zorder=3)
        ax_h.set_xlim(0, 1.15)
        ax_h.set_yticks(list(y_pos))
        ax_h.set_yticklabels(labels, fontsize=10, color=TEXT)
        ax_h.set_xticks([])
        ax_h.invert_yaxis()
        ax_h.set_title("Звички сьогодні", color=TEXT, fontsize=11, pad=8)
        ax_h.grid(False)
        for spine in ax_h.spines.values():
            spine.set_visible(False)

        # Підписи на барах
        for i, (val, hkey) in enumerate(zip(values, HABITS)):
            v = entry.get(hkey)
            label = "✅" if v is True else "❌"
            ax_h.text(1.08, i, label, va="center", ha="center",
                      fontsize=11, color=TEXT)

        # Право: мінi-графік ваги за 14 днів
        ax_w = axes[1]
        ax_w.set_facecolor(PANEL)

        today_d = datetime.strptime(today_str, "%Y-%m-%d").date()
        w_dates = [today_d - timedelta(days=i) for i in range(13, -1, -1)]
        w_vals = [wdata.get(d.isoformat()) for d in w_dates]
        present = [(i, v) for i, v in enumerate(w_vals) if v is not None]

        if len(present) >= 2:
            xi = [p[0] for p in present]
            yi = [p[1] for p in present]
            ax_w.fill_between(xi, yi, min(yi) - 0.5, alpha=0.2, color=GREEN)
            ax_w.plot(xi, yi, color=GREEN, linewidth=2.5, zorder=4)
            ax_w.scatter([xi[-1]], [yi[-1]], color=GREEN, s=60, zorder=5)
            ax_w.axhline(78.0, color=BLUE, linewidth=1.2, linestyle="--",
                         alpha=0.7, label="ціль 78")
            ax_w.set_ylim(min(yi) - 1.5, max(yi) + 1.5)
            ax_w.set_xticks([])
            ax_w.set_title("Вага, кг", color=TEXT, fontsize=11, pad=8)
            for spine in ax_w.spines.values():
                spine.set_edgecolor(BORDER)
            ax_w.tick_params(colors=MUTED)
            ax_w.yaxis.tick_right()
            # Останнє значення
            ax_w.annotate(f"{yi[-1]:.1f} кг",
                          xy=(xi[-1], yi[-1]),
                          xytext=(-22, 10), textcoords="offset points",
                          color=GREEN, fontsize=10, fontweight="bold")
        else:
            ax_w.text(0.5, 0.5, "Немає даних", ha="center", va="center",
                      color=MUTED, fontsize=10, transform=ax_w.transAxes)
            ax_w.set_title("Вага, кг", color=TEXT, fontsize=11, pad=8)
            for spine in ax_w.spines.values():
                spine.set_visible(False)

        done = sum(1 for hkey in HABITS if entry.get(hkey) is True)
        fig.suptitle(f"📊 Підсумок дня  {today_str[8:]}.{today_str[5:7]}  ·  {done}/{len(HABITS)} звичок",
                     fontsize=13, color=TEXT, fontweight="bold", y=1.02)

        fig.tight_layout(pad=1.5)
        return _buf(fig)
    except Exception as e:
        print(f"[charts] day_dashboard error: {e}")
        return None


# ── 3. ТИЖНЕВИЙ ДАШБОРД ───────────────────────────────────────────────────────

def plot_weekly_dashboard(days: int = 7) -> bytes | None:
    """
    Комбінований дашборд тижня:
    - Зверху: bar chart звичок по днях
    - Знизу ліво: лінія ваги
    - Знизу право: streak counters
    """
    if not HAS_MPL:
        return None
    try:
        _rc()
        raw = _load_habits()
        wdata = _load_weight()

        today = date.today()
        week_dates = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
        day_labels = [d.strftime("%a\n%d.%m") for d in week_dates]
        # Українські дні
        UA_DAYS = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср",
                   "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Нд"}
        day_labels = [UA_DAYS.get(d.strftime("%a"), d.strftime("%a")) + "\n" + d.strftime("%d.%m")
                      for d in week_dates]

        HABITS = ["shower", "run", "water", "tea", "sauna"]
        HABIT_N = len(HABITS)

        # Кількість виконаних звичок по дню
        done_per_day = []
        for d in week_dates:
            entry = raw.get(d.isoformat(), {}) or {}
            done_per_day.append(sum(1 for h in HABITS if entry.get(h) is True))

        # Вага за 14 днів
        w14_dates = [today - timedelta(days=i) for i in range(13, -1, -1)]
        w14_vals = [wdata.get(d.isoformat()) for d in w14_dates]

        # Streaks
        streaks = {}
        for hkey in HABITS:
            s = 0
            for d in [today - timedelta(days=i) for i in range(30)]:
                entry = raw.get(d.isoformat(), {}) or {}
                if entry.get(hkey) is True:
                    s += 1
                else:
                    break
            streaks[hkey] = s

        fig = plt.figure(figsize=(12, 8), facecolor=BG)
        gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.45, wspace=0.35)

        # ── TOP: звички по дням (stacked bar) ────────────────────────────────
        ax_top = fig.add_subplot(gs[0, :])
        ax_top.set_facecolor(PANEL)

        x = np.arange(days)
        bottom = np.zeros(days)
        for hkey in HABITS:
            vals = []
            for d in week_dates:
                entry = raw.get(d.isoformat(), {}) or {}
                vals.append(1 if entry.get(hkey) is True else 0)
            ax_top.bar(x, vals, bottom=bottom, color=HABIT_COLORS[hkey],
                       width=0.65, label=HABIT_LABELS[hkey], alpha=0.9, zorder=3)
            bottom += np.array(vals, dtype=float)

        ax_top.set_xticks(x)
        ax_top.set_xticklabels(day_labels, fontsize=9, color=TEXT)
        ax_top.set_yticks(range(HABIT_N + 1))
        ax_top.set_ylim(0, HABIT_N + 0.5)
        ax_top.set_ylabel("Звичок виконано", color=MUTED, fontsize=9)
        ax_top.set_title("📅 Звички по днях тижня", color=TEXT, fontsize=11, fontweight="bold")
        ax_top.grid(axis="y", alpha=0.3, zorder=0)
        ax_top.set_axisbelow(True)
        for spine in ax_top.spines.values():
            spine.set_edgecolor(BORDER)
        ax_top.tick_params(axis="x", length=0)

        # Значення над барами
        for i, val in enumerate(done_per_day):
            if val > 0:
                ax_top.text(i, val + 0.05, str(val),
                            ha="center", va="bottom", color=TEXT,
                            fontsize=10, fontweight="bold")

        legend = ax_top.legend(loc="upper left", fontsize=8,
                               framealpha=0.4, facecolor=PANEL,
                               edgecolor=BORDER, labelcolor=TEXT,
                               ncol=len(HABITS))

        # ── BOTTOM LEFT: вага ─────────────────────────────────────────────────
        ax_w = fig.add_subplot(gs[1, 0])
        ax_w.set_facecolor(PANEL)

        present_w = [(i, v) for i, v in enumerate(w14_vals) if v is not None]
        if len(present_w) >= 2:
            xi = [p[0] for p in present_w]
            yi = [p[1] for p in present_w]
            ax_w.fill_between(xi, yi, min(yi) - 0.5, alpha=0.18, color=GREEN)
            ax_w.plot(xi, yi, color=GREEN, linewidth=2.5, zorder=4)
            ax_w.scatter(xi, yi, color=GREEN, s=30, zorder=5, alpha=0.7)
            ax_w.axhline(78.0, color=BLUE, linewidth=1.2, linestyle="--",
                         alpha=0.7)
            ax_w.set_ylim(min(yi) - 1.5, max(yi) + 1.5)
            # X-axis кожні 7 днів
            ax_w.set_xticks([0, 6, 13])
            ax_w.set_xticklabels([w14_dates[0].strftime("%d.%m"),
                                   w14_dates[6].strftime("%d.%m"),
                                   w14_dates[13].strftime("%d.%m")],
                                  fontsize=8, color=MUTED)
            ax_w.tick_params(colors=MUTED)
            # Текст останньої ваги
            ax_w.annotate(f"{yi[-1]:.1f} кг",
                          xy=(xi[-1], yi[-1]),
                          xytext=(-30, 12), textcoords="offset points",
                          color=GREEN, fontsize=11, fontweight="bold",
                          arrowprops=dict(arrowstyle="-", color=GREEN, alpha=0.5))
            # Ціль
            ax_w.text(1, 78.0 + 0.15, "ціль 78 кг",
                      color=BLUE, fontsize=8, alpha=0.8)
        else:
            ax_w.text(0.5, 0.5, "Немає даних", ha="center", va="center",
                      color=MUTED, fontsize=10, transform=ax_w.transAxes)

        ax_w.set_title("⚖️ Вага (14 днів)", color=TEXT, fontsize=11, fontweight="bold")
        for spine in ax_w.spines.values():
            spine.set_edgecolor(BORDER)
        ax_w.grid(axis="y", alpha=0.2)

        # ── BOTTOM RIGHT: streak counters ─────────────────────────────────────
        ax_s = fig.add_subplot(gs[1, 1])
        ax_s.set_facecolor(PANEL)
        ax_s.axis("off")
        ax_s.set_title("🔥 Стрік (днів поспіль)", color=TEXT, fontsize=11, fontweight="bold")

        streak_items = [(HABIT_LABELS[h], streaks[h], HABIT_COLORS[h]) for h in HABITS]
        for i, (label, streak, color) in enumerate(streak_items):
            y = 0.82 - i * 0.19
            # Фон-прямокутник
            rect = mpatches.FancyBboxPatch(
                (0.02, y - 0.07), 0.96, 0.16,
                boxstyle="round,pad=0.02",
                linewidth=0,
                facecolor="#1C2128",
                transform=ax_s.transAxes
            )
            ax_s.add_patch(rect)
            ax_s.text(0.08, y + 0.01, label, transform=ax_s.transAxes,
                      fontsize=10, color=TEXT, va="center")
            streak_color = color if streak > 0 else MUTED
            flame = "🔥" if streak >= 3 else ("✅" if streak >= 1 else "💤")
            ax_s.text(0.88, y + 0.01, f"{flame} {streak}д",
                      transform=ax_s.transAxes,
                      fontsize=10, color=streak_color,
                      va="center", ha="right", fontweight="bold")

        fig.suptitle(
            f"📊 Тижневий дашборд  {week_dates[0].strftime('%d.%m')}–{week_dates[-1].strftime('%d.%m.%Y')}",
            fontsize=14, color=TEXT, fontweight="bold", y=1.01
        )

        return _buf(fig)
    except Exception as e:
        print(f"[charts] weekly_dashboard error: {e}")
        return None


# ── 4. МІСЯЧНИЙ ДАШБОРД ───────────────────────────────────────────────────────

def plot_monthly_dashboard(year: int = None, month: int = None) -> bytes | None:
    """
    Місячний дашборд:
    - Heatmap звичок за місяць
    - Лінія ваги за місяць
    """
    if not HAS_MPL:
        return None
    try:
        _rc()
        import calendar as _cal

        now = datetime.now(timezone.utc) + timedelta(hours=2)
        if year is None:
            year = now.year
        if month is None:
            month = now.month

        # Попередній місяць якщо викликаємо 1-го числа
        if now.day == 1 and year == now.year and month == now.month:
            first = now.replace(day=1) - timedelta(days=1)
            year, month = first.year, first.month

        _, n_days = _cal.monthrange(year, month)
        month_dates = [date(year, month, d) for d in range(1, n_days + 1)]

        raw = _load_habits()
        wdata = _load_weight()
        HABITS = ["shower", "run", "water", "tea", "sauna"]

        fig = plt.figure(figsize=(14, 8), facecolor=BG)
        gs = gridspec.GridSpec(2, 1, figure=fig, hspace=0.5,
                               height_ratios=[1.8, 1])

        # ── TOP: heatmap звичок за місяць ─────────────────────────────────────
        ax_h = fig.add_subplot(gs[0])
        ax_h.set_facecolor(BG)
        ax_h.axis("off")

        CELL = 0.85  # розмір клітинки
        for hi, hkey in enumerate(HABITS):
            color = HABIT_COLORS[hkey]
            for di, d in enumerate(month_dates):
                entry = raw.get(d.isoformat(), {}) or {}
                v = entry.get(hkey)
                if v is True:
                    fc = color
                    alpha = 0.92
                elif v is False:
                    fc = "#21262D"
                    alpha = 1.0
                else:
                    fc = "#1C2128"
                    alpha = 1.0
                rect = mpatches.FancyBboxPatch(
                    (di * 1.0, -(hi * 1.15)),
                    CELL, CELL * 0.9,
                    boxstyle="round,pad=0.05",
                    linewidth=0,
                    facecolor=fc,
                    alpha=alpha,
                    transform=ax_h.transData
                )
                ax_h.add_patch(rect)

            # Підпис звички ліворуч
            ax_h.text(-1.5, -(hi * 1.15) + 0.35, HABIT_LABELS[hkey],
                      fontsize=9, color=TEXT, va="center", ha="right")

        # X-axis: номери днів кожні 5
        ax_h.set_xlim(-2, n_days + 0.5)
        ax_h.set_ylim(-len(HABITS) * 1.15 - 0.3, 1.2)
        for di in range(0, n_days, 5):
            ax_h.text(di + 0.4, 0.9, str(di + 1),
                      fontsize=7.5, color=MUTED, ha="center")
        # Останній день
        ax_h.text(n_days - 0.6, 0.9, str(n_days),
                  fontsize=7.5, color=MUTED, ha="center")

        UA_MONTHS = {1:"Січень",2:"Лютий",3:"Березень",4:"Квітень",
                     5:"Травень",6:"Червень",7:"Липень",8:"Серпень",
                     9:"Вересень",10:"Жовтень",11:"Листопад",12:"Грудень"}
        ax_h.set_title(f"📋 Звички за {UA_MONTHS[month]} {year}",
                       color=TEXT, fontsize=12, fontweight="bold", pad=14)

        # ── BOTTOM: вага за місяць ─────────────────────────────────────────────
        ax_w = fig.add_subplot(gs[1])
        ax_w.set_facecolor(PANEL)

        w_vals = [(d, wdata.get(d.isoformat())) for d in month_dates]
        present_w = [(d, v) for d, v in w_vals if v is not None]

        if len(present_w) >= 2:
            xi = [d.day for d, _ in present_w]
            yi = [v for _, v in present_w]
            ax_w.fill_between(xi, yi, min(yi) - 0.5, alpha=0.18, color=GREEN)
            ax_w.plot(xi, yi, color=GREEN, linewidth=2.8, zorder=4)
            ax_w.scatter(xi, yi, color=GREEN, s=35, zorder=5, alpha=0.8)
            ax_w.axhline(78.0, color=BLUE, linewidth=1.2, linestyle="--",
                         alpha=0.7, label="ціль 78 кг")
            ax_w.set_xlim(1, n_days)
            ax_w.set_ylim(min(yi) - 2, max(yi) + 2)
            ax_w.legend(fontsize=8, loc="upper right",
                        framealpha=0.3, facecolor=PANEL,
                        edgecolor=BORDER, labelcolor=TEXT)
            # Тренд тексту
            diff = yi[-1] - yi[0]
            sign = "+" if diff > 0 else ""
            trend_color = RED if diff > 0.5 else (GREEN if diff < -0.5 else MUTED)
            ax_w.text(0.02, 0.9,
                      f"Старт: {yi[0]:.1f} кг → Кінець: {yi[-1]:.1f} кг  ({sign}{diff:.1f} кг)",
                      transform=ax_w.transAxes,
                      fontsize=9, color=trend_color)
        else:
            ax_w.text(0.5, 0.5, "Недостатньо даних",
                      ha="center", va="center", color=MUTED, fontsize=10,
                      transform=ax_w.transAxes)

        ax_w.set_title(f"⚖️ Вага за {UA_MONTHS[month]}",
                       color=TEXT, fontsize=11, fontweight="bold")
        ax_w.tick_params(colors=MUTED)
        for spine in ax_w.spines.values():
            spine.set_edgecolor(BORDER)
        ax_w.grid(axis="y", alpha=0.2)

        fig.suptitle(
            f"📆 Місячний дашборд — {UA_MONTHS[month]} {year}",
            fontsize=14, color=TEXT, fontweight="bold", y=1.02
        )

        return _buf(fig)
    except Exception as e:
        print(f"[charts] monthly_dashboard error: {e}")
        return None


# ── 5. ANOMALY CHART (для проактивних сповіщень) ──────────────────────────────

def plot_weight_anomaly(days: int = 14) -> bytes | None:
    """
    Міні-графік ваги з підсвіченим трендом — для проактивного сповіщення.
    """
    if not HAS_MPL:
        return None
    try:
        _rc()
        wdata = _load_weight()
        today = date.today()
        all_dates = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
        present = [(d, wdata.get(d.isoformat())) for d in all_dates
                   if wdata.get(d.isoformat()) is not None]
        if len(present) < 3:
            return None

        xi = list(range(len(present)))
        yi = [v for _, v in present]
        xlabels = [d.strftime("%d.%m") for d, _ in present]

        fig, ax = plt.subplots(figsize=(9, 4), facecolor=BG)
        ax.set_facecolor(PANEL)

        # Тренд лінія
        z = np.polyfit(xi, yi, 1)
        p = np.poly1d(z)
        trend_y = [p(x) for x in xi]
        trend_color = RED if z[0] > 0.05 else (GREEN if z[0] < -0.05 else MUTED)

        ax.fill_between(xi, yi, min(yi) - 0.5, alpha=0.15, color=GREEN)
        ax.plot(xi, yi, color=GREEN, linewidth=2.5, zorder=4, label="Вага")
        ax.plot(xi, trend_y, color=trend_color, linewidth=1.5,
                linestyle="--", alpha=0.8, zorder=3, label="Тренд")
        ax.scatter([xi[-1]], [yi[-1]], color=GREEN, s=70, zorder=5)
        ax.axhline(78.0, color=BLUE, linewidth=1.0, linestyle=":", alpha=0.6)

        tick_step = max(1, len(xi) // 7)
        ax.set_xticks(xi[::tick_step])
        ax.set_xticklabels(xlabels[::tick_step], fontsize=8, color=MUTED)
        ax.tick_params(colors=MUTED)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER)
        ax.grid(axis="y", alpha=0.2)
        ax.legend(fontsize=8, framealpha=0.3, facecolor=PANEL,
                  edgecolor=BORDER, labelcolor=TEXT)

        diff = yi[-1] - yi[0]
        sign = "+" if diff > 0 else ""
        ax.set_title(f"⚖️ Вага  {sign}{diff:.1f} кг за {len(present)} днів  ·  Зараз: {yi[-1]:.1f} кг",
                     color=TEXT, fontsize=11, fontweight="bold")

        fig.tight_layout(pad=1.2)
        return _buf(fig)
    except Exception as e:
        print(f"[charts] weight_anomaly error: {e}")
        return None
