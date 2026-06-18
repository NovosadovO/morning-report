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
except Exception:
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
    "spray":  PURPLE,
}
HABIT_LABELS = {
    "shower": "Душ",
    "run":    "Біг",
    "water":  "Вода",
    "tea":    "Чай",
    "sauna":  "Сауна",
    "spray":  "Спрей",
}
# Emoji для підписів (відображаються через PIL overlay або якщо шрифт підтримує)
HABIT_EMOJIS = {
    "shower": "🚿",
    "run":    "🏃",
    "water":  "💧",
    "tea":    "🍵",
    "sauna":  "🧖",
    "spray":  "💈",
}

_DIR = os.path.dirname(os.path.abspath(__file__))


def _rc():
    # Намагаємось підключити emoji-шрифт якщо є
    import matplotlib.font_manager as _fm
    _emoji_fonts = [f.name for f in _fm.fontManager.ttflist
                    if "noto" in f.name.lower() and "emoji" in f.name.lower()]
    _sans = ["DejaVu Sans"] + _emoji_fonts
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
        "font.family":      "sans-serif",
        "font.sans-serif":  _sans,
    })


def _buf(fig, dpi: int = 200) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                facecolor=BG, edgecolor="none", pad_inches=0.25)
    plt.close(fig)
    buf.seek(0)
    data = buf.read()
    # Telegram стискає фото > ~5 МБ або сторону > 1280px надмірно — даунскейлимо до якісного 2000px max
    try:
        from PIL import Image as _PILImg
        import io as _io2
        _im = _PILImg.open(_io2.BytesIO(data))
        _maxside = 2200
        if max(_im.size) > _maxside:
            _ratio = _maxside / max(_im.size)
            _im = _im.resize((int(_im.size[0]*_ratio), int(_im.size[1]*_ratio)), _PILImg.LANCZOS)
            _out = _io2.BytesIO()
            _im.convert("RGB").save(_out, format="PNG", optimize=True)
            data = _out.getvalue()
    except Exception:
        pass
    return data


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
        ax.set_yticklabels([HABIT_LABELS[h] for h in HABITS], fontsize=22, color=TEXT)
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
                    linewidth=3.0,
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
            fontsize=20, color=MUTED
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
                  fontsize=20, framealpha=0.3,
                  facecolor=PANEL, edgecolor=BORDER,
                  labelcolor=TEXT)

        ax.set_title(f"Звички — останні {days} днів", fontsize=24,
                     color=TEXT, pad=12, fontweight="bold")

        fig.tight_layout(pad=1.2)
        return _buf(fig)
    except Exception as e:
        print(f"[charts] habits_heatmap error: {e}")
        return None


# ── 2. ДАШБОРД ДНЯ ────────────────────────────────────────────────────────────

def plot_day_dashboard(today_str: str = None) -> bytes | None:
    """
    Дашборд одного дня: звички + вага з трендом + аналіз.
    Відправляється з підсумком дня (19/20:xx).
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

        # ── layout: 3 панелі ───────────────────────────────────────────────────
        fig = plt.figure(figsize=(28, 18), facecolor=BG)
        gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.60, wspace=0.40,
                               height_ratios=[1.2, 1])

        # ── TOP FULL: вага за 30 днів з трендом ───────────────────────────────
        ax_w = fig.add_subplot(gs[0, :])
        ax_w.set_facecolor(PANEL)

        today_d = datetime.strptime(today_str, "%Y-%m-%d").date()
        w_dates = [today_d - timedelta(days=i) for i in range(29, -1, -1)]
        w_vals  = [wdata.get(d.isoformat()) for d in w_dates]
        present = [(i, v) for i, v in enumerate(w_vals) if v is not None]

        if len(present) >= 2:
            xi = np.array([p[0] for p in present])
            yi = np.array([p[1] for p in present])

            # Зона цілі
            ax_w.axhspan(77.0, 79.0, alpha=0.08, color=BLUE, label="зона цілі 77–79")

            # Fill + лінія
            ax_w.fill_between(xi, yi, min(yi) - 0.5, alpha=0.18, color=GREEN)
            ax_w.plot(xi, yi, color=GREEN, linewidth=4.0, zorder=4, label="вага")
            ax_w.scatter(xi, yi, color=GREEN, s=84, zorder=5, alpha=0.7)
            # Останній маркер великий
            ax_w.scatter([xi[-1]], [yi[-1]], color=GREEN, s=270, zorder=6)

            # Ціль-лінія
            ax_w.axhline(78.0, color=BLUE, linewidth=3.0, linestyle="--",
                         alpha=0.9, label="ціль 78 кг")

            # Лінія тренду (linear regression)
            z    = np.polyfit(xi, yi, 1)
            p    = np.poly1d(z)
            trend_x = np.array([xi[0], xi[-1]])
            trend_y = p(trend_x)
            slope_per_week = z[0] * 7
            trend_color = RED if z[0] > 0.02 else (GREEN if z[0] < -0.02 else MUTED)
            ax_w.plot(trend_x, trend_y, color=trend_color, linewidth=3.0,
                      linestyle=":", zorder=3, label=f"тренд")

            # Маркер найкращої ваги за 30 днів
            best_i = int(np.argmin(yi))
            ax_w.scatter([xi[best_i]], [yi[best_i]], color=YELLOW, s=100,
                         zorder=7, marker="D")
            ax_w.annotate(f"мін {yi[best_i]:.1f}",
                          xy=(xi[best_i], yi[best_i]),
                          xytext=(6, -16), textcoords="offset points",
                          color=YELLOW, fontsize=26)

            # Підпис останнього значення
            ax_w.annotate(f"{yi[-1]:.1f} кг",
                          xy=(xi[-1], yi[-1]),
                          xytext=(-38, 12), textcoords="offset points",
                          color=GREEN, fontsize=30, fontweight="bold",
                          arrowprops=dict(arrowstyle="-", color=GREEN, alpha=0.4))

            # Підпис тренду
            sign = "+" if slope_per_week > 0 else ""
            ax_w.text(0.02, 0.93,
                      f"{sign}{slope_per_week:.2f} кг/тижд",
                      transform=ax_w.transAxes,
                      fontsize=28, color=trend_color, fontweight="bold",
                      va="top")

            ax_w.set_ylim(min(yi) - 1.8, max(yi) + 1.8)

            # X-тіки: дати кожні 7 днів
            tick_ix = [0, 7, 14, 21, 29]
            tick_ix = [t for t in tick_ix if t < len(w_dates)]
            ax_w.set_xticks(tick_ix)
            ax_w.set_xticklabels(
                [w_dates[t].strftime("%d.%m") for t in tick_ix],
                fontsize=26, color=MUTED
            )

            ax_w.legend(loc="upper right", fontsize=26, framealpha=0.3,
                        facecolor=PANEL, edgecolor=BORDER, labelcolor=TEXT,
                        ncol=4)
        else:
            ax_w.text(0.5, 0.5, "Немає даних по вазі",
                      ha="center", va="center", color=MUTED, fontsize=30,
                      transform=ax_w.transAxes)

        ax_w.set_title("Вага за 30 днів — тренд та ціль", color=TEXT,
                       fontsize=30, fontweight="bold")
        for spine in ax_w.spines.values():
            spine.set_edgecolor(BORDER)
        ax_w.tick_params(colors=MUTED)
        ax_w.grid(axis="y", alpha=0.2)

        # ── BOTTOM LEFT: звички сьогодні ──────────────────────────────────────
        ax_h = fig.add_subplot(gs[1, 0])
        ax_h.set_facecolor(PANEL)

        y_pos  = list(range(len(HABITS)))
        values = []
        colors_h = []
        for hkey in HABITS:
            v = entry.get(hkey)
            if v is True:
                values.append(1.0)
                colors_h.append(HABIT_COLORS[hkey])
            else:
                values.append(0.12)
                colors_h.append("#21262D")

        ax_h.barh(y_pos, values, height=0.6, color=colors_h, zorder=3)
        ax_h.set_xlim(0, 1.2)
        ax_h.set_yticks(y_pos)
        ax_h.set_yticklabels([HABIT_LABELS[h] for h in HABITS],
                              fontsize=28, color=TEXT)
        ax_h.set_xticks([])
        ax_h.invert_yaxis()
        ax_h.set_title("Звички сьогодні", color=TEXT, fontsize=30, pad=8)
        ax_h.grid(False)
        for spine in ax_h.spines.values():
            spine.set_visible(False)
        for i, hkey in enumerate(HABITS):
            label = "✓" if entry.get(hkey) is True else "✗"
            ax_h.text(1.13, i, label, va="center", ha="center",
                      fontsize=30, color=TEXT)

        done = sum(1 for hkey in HABITS if entry.get(hkey) is True)

        # ── BOTTOM RIGHT: стрік 7 днів по кожній звичці ───────────────────────
        ax_s = fig.add_subplot(gs[1, 1])
        ax_s.set_facecolor(PANEL)

        # Стрік + % за 7 днів
        week_dates_r = [today_d - timedelta(days=i) for i in range(6, -1, -1)]
        streak_data = []
        for hkey in HABITS:
            streak = 0
            for d in [today_d - timedelta(days=i) for i in range(30)]:
                e = raw.get(d.isoformat(), {}) or {}
                if e.get(hkey) is True:
                    streak += 1
                else:
                    break
            week_done = sum(
                1 for d in week_dates_r
                if (raw.get(d.isoformat(), {}) or {}).get(hkey) is True
            )
            pct = week_done / 7 * 100
            streak_data.append((hkey, streak, pct))

        # Grouped bar: стрік (normalized /30) vs % за тиждень
        n = len(HABITS)
        x = np.arange(n)
        w = 0.38
        streak_norm = [min(s / 30, 1.0) for _, s, _ in streak_data]
        pct_norm    = [p / 100 for _, _, p in streak_data]
        colors_streak = [HABIT_COLORS[h] for h in HABITS]

        ax_s.bar(x - w/2, streak_norm, width=w, color=colors_streak,
                 alpha=0.85, zorder=3, label="стрік /30д")
        ax_s.bar(x + w/2, pct_norm, width=w,
                 color=[PURPLE]*n, alpha=0.75, zorder=3, label="% тиждень")

        ax_s.set_xticks(x)
        ax_s.set_xticklabels([HABIT_LABELS[h].split()[-1] for h in HABITS],
                              fontsize=26, color=TEXT)
        ax_s.set_ylim(0, 1.2)
        ax_s.set_yticks([0, 0.5, 1.0])
        ax_s.set_yticklabels(["0", "50%", "100%"], fontsize=26, color=MUTED)
        ax_s.set_title("Стрік vs тиждень", color=TEXT, fontsize=30, pad=8)
        ax_s.legend(fontsize=24, framealpha=0.3, facecolor=PANEL,
                    edgecolor=BORDER, labelcolor=TEXT)
        for spine in ax_s.spines.values():
            spine.set_edgecolor(BORDER)
        ax_s.grid(axis="y", alpha=0.2)

        # Підписи стріків над барами
        for i, (hkey, streak, pct) in enumerate(streak_data):
            ax_s.text(i - w/2, streak_norm[i] + 0.03, f"{streak}д",
                      ha="center", fontsize=26, color=TEXT)
            ax_s.text(i + w/2, pct_norm[i] + 0.03, f"{int(pct)}%",
                      ha="center", fontsize=26, color=TEXT)

        fig.suptitle(
            f"Підсумок {today_str[8:]}.{today_str[5:7]}  ·  {done}/{len(HABITS)} звичок",
            fontsize=32, color=TEXT, fontweight="bold", y=1.02
        )

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

        fig = plt.figure(figsize=(28, 18), facecolor=BG)
        gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.55, wspace=0.40)

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
        ax_top.set_xticklabels(day_labels, fontsize=28, color=TEXT)
        ax_top.set_yticks(range(HABIT_N + 1))
        ax_top.set_ylim(0, HABIT_N + 0.5)
        ax_top.set_ylabel("Звичок виконано", color=MUTED, fontsize=28)
        ax_top.set_title("Звички по днях тижня", color=TEXT, fontsize=30, fontweight="bold")
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
                            fontsize=28, fontweight="bold")

        # Лінія тренду звичок (рухома середня або polyfit)
        if len(done_per_day) >= 3:
            xi_t = np.arange(days)
            yi_t = np.array(done_per_day, dtype=float)
            z_t  = np.polyfit(xi_t, yi_t, 1)
            p_t  = np.poly1d(z_t)
            trend_y_t = p_t(xi_t)
            t_color = GREEN if z_t[0] >= 0 else RED
            ax_top.plot(xi_t, trend_y_t, color=t_color, linewidth=4.0,
                        linestyle="--", zorder=5, label="тренд")
            sign_t = "+" if z_t[0] >= 0 else ""
            ax_top.text(0.02, 0.93,
                        f"тренд: {sign_t}{z_t[0]:.2f} звич/день",
                        transform=ax_top.transAxes,
                        fontsize=26, color=t_color, va="top")

        legend = ax_top.legend(loc="upper right", fontsize=26,
                               framealpha=0.4, facecolor=PANEL,
                               edgecolor=BORDER, labelcolor=TEXT,
                               ncol=len(HABITS) + 1)

        # ── BOTTOM LEFT: вага ─────────────────────────────────────────────────
        ax_w = fig.add_subplot(gs[1, 0])
        ax_w.set_facecolor(PANEL)

        present_w = [(i, v) for i, v in enumerate(w14_vals) if v is not None]
        if len(present_w) >= 2:
            xi = np.array([p[0] for p in present_w])
            yi = np.array([p[1] for p in present_w])

            # Зона цілі
            ax_w.axhspan(77.0, 79.0, alpha=0.08, color=BLUE)

            ax_w.fill_between(xi, yi, min(yi) - 0.5, alpha=0.18, color=GREEN)
            ax_w.plot(xi, yi, color=GREEN, linewidth=4.0, zorder=4)
            ax_w.scatter(xi, yi, color=GREEN, s=90, zorder=5, alpha=0.7)
            ax_w.axhline(78.0, color=BLUE, linewidth=3.0, linestyle="--", alpha=0.7)

            # Лінія тренду
            z_w = np.polyfit(xi, yi, 1)
            p_w = np.poly1d(z_w)
            trend_yw = p_w(np.array([xi[0], xi[-1]]))
            t_col_w = RED if z_w[0] > 0.02 else (GREEN if z_w[0] < -0.02 else MUTED)
            ax_w.plot([xi[0], xi[-1]], trend_yw, color=t_col_w,
                      linewidth=3.0, linestyle=":", zorder=3)
            slope_week = z_w[0] * 7
            sign_w = "+" if slope_week > 0 else ""
            ax_w.text(0.03, 0.06, f"{sign_w}{slope_week:.2f} кг/тижд",
                      transform=ax_w.transAxes, fontsize=26, color=t_col_w,
                      fontweight="bold")

            ax_w.set_ylim(min(yi) - 1.5, max(yi) + 1.5)
            # X-axis кожні 7 днів
            ax_w.set_xticks([0, 6, 13])
            ax_w.set_xticklabels([w14_dates[0].strftime("%d.%m"),
                                   w14_dates[6].strftime("%d.%m"),
                                   w14_dates[13].strftime("%d.%m")],
                                  fontsize=26, color=MUTED)
            ax_w.tick_params(colors=MUTED)
            # Текст останньої ваги
            ax_w.annotate(f"{yi[-1]:.1f} кг",
                          xy=(xi[-1], yi[-1]),
                          xytext=(-30, 12), textcoords="offset points",
                          color=GREEN, fontsize=30, fontweight="bold",
                          arrowprops=dict(arrowstyle="-", color=GREEN, alpha=0.5))
            # Ціль
            ax_w.text(1, 78.0 + 0.15, "ціль 78 кг",
                      color=BLUE, fontsize=26, alpha=0.8)
        else:
            ax_w.text(0.5, 0.5, "Немає даних", ha="center", va="center",
                      color=MUTED, fontsize=28, transform=ax_w.transAxes)

        ax_w.set_title("Вага (14 днів) + тренд", color=TEXT, fontsize=30, fontweight="bold")
        for spine in ax_w.spines.values():
            spine.set_edgecolor(BORDER)
        ax_w.grid(axis="y", alpha=0.2)

        # ── BOTTOM RIGHT: streak counters ─────────────────────────────────────
        ax_s = fig.add_subplot(gs[1, 1])
        ax_s.set_facecolor(PANEL)
        ax_s.axis("off")
        ax_s.set_title("Стрік (днів поспіль)", color=TEXT, fontsize=30, fontweight="bold")

        streak_items = [(HABIT_LABELS[h], streaks[h], HABIT_COLORS[h]) for h in HABITS]
        for i, (label, streak, color) in enumerate(streak_items):
            y = 0.82 - i * 0.19
            # Фон-прямокутник
            rect = mpatches.FancyBboxPatch(
                (0.02, y - 0.07), 0.96, 0.16,
                boxstyle="round,pad=0.02",
                linewidth=3.0,
                facecolor="#1C2128",
                transform=ax_s.transAxes
            )
            ax_s.add_patch(rect)
            ax_s.text(0.08, y + 0.01, label, transform=ax_s.transAxes,
                      fontsize=28, color=TEXT, va="center")
            streak_color = color if streak > 0 else MUTED
            flame = "🔥" if streak >= 3 else ("✓" if streak >= 1 else "💤")
            ax_s.text(0.88, y + 0.01, f"{flame} {streak}д",
                      transform=ax_s.transAxes,
                      fontsize=28, color=streak_color,
                      va="center", ha="right", fontweight="bold")

        fig.suptitle(
            f"Тижневий дашборд  {week_dates[0].strftime('%d.%m')}–{week_dates[-1].strftime('%d.%m.%Y')}",
            fontsize=26, color=TEXT, fontweight="bold", y=1.01
        )

        return _buf(fig)
    except Exception as e:
        print(f"[charts] weekly_dashboard error: {e}")
        return None


# ── 4. МІСЯЧНИЙ ДАШБОРД ───────────────────────────────────────────────────────

def plot_monthly_dashboard(year: int = None, month: int = None) -> bytes | None:
    """
    6-місячний дашборд (до сьогодні):
    - Heatmap звичок за 6 місяців
    - Лінія ваги за 6 місяців
    """
    if not HAS_MPL:
        return None
    try:
        _rc()
        import calendar as _cal
        from datetime import date as _date_cls2

        now = datetime.now(timezone.utc) + timedelta(hours=2)
        today = now.date()

        # Від 1 січня 2026 до сьогодні
        end_date = today
        start_date = _date_cls2(2026, 1, 1)
        all_dates = [start_date + timedelta(days=i)
                     for i in range((end_date - start_date).days + 1)]

        raw = _load_habits()
        wdata = _load_weight()
        HABITS = ["shower", "run", "water", "tea", "sauna"]

        UA_MONTHS = {1:"Січ",2:"Лют",3:"Бер",4:"Кві",5:"Тра",6:"Чер",
                     7:"Лип",8:"Сер",9:"Вер",10:"Жов",11:"Лис",12:"Гру"}
        UA_MONTHS_FULL = {1:"Січень",2:"Лютий",3:"Березень",4:"Квітень",
                          5:"Травень",6:"Червень",7:"Липень",8:"Серпень",
                          9:"Вересень",10:"Жовтень",11:"Листопад",12:"Грудень"}

        # ── Фігура: широка під весь рік ──────────────────────────────────────
        n_days = len(all_dates)
        fig_w = max(20, n_days * 0.10)  # ~0.1 дюйм на день, мін 20
        fig = plt.figure(figsize=(fig_w, 16), facecolor=BG)
        gs = gridspec.GridSpec(2, 1, figure=fig, hspace=0.6,
                               height_ratios=[2, 1])

        # ── TOP: heatmap звичок за 6 місяців ─────────────────────────────────
        ax_h = fig.add_subplot(gs[0])
        ax_h.set_facecolor(BG)
        ax_h.axis("off")

        CELL = 1.0
        GAP  = 0.22   # зазор між клітинками

        for hi, hkey in enumerate(HABITS):
            color = HABIT_COLORS[hkey]
            for di, d in enumerate(all_dates):
                entry = raw.get(d.isoformat(), {}) or {}
                v = entry.get(hkey)
                if v is True:
                    # Зелене коло з галочкою
                    circle = plt.Circle(
                        (di * (CELL + GAP) + CELL / 2, -(hi * 1.6) + 0.3),
                        0.44, color=color, alpha=0.9, zorder=2
                    )
                    ax_h.add_patch(circle)
                    ax_h.text(
                        di * (CELL + GAP) + CELL / 2, -(hi * 1.6) + 0.3,
                        "✓", fontsize=22, ha="center", va="center",
                        color="white", fontweight="bold", zorder=3
                    )
                elif v is False:
                    # Темне коло з хрестиком
                    circle = plt.Circle(
                        (di * (CELL + GAP) + CELL / 2, -(hi * 1.6) + 0.3),
                        0.44, color="#21262D", alpha=1.0, zorder=2
                    )
                    ax_h.add_patch(circle)
                    ax_h.text(
                        di * (CELL + GAP) + CELL / 2, -(hi * 1.6) + 0.3,
                        "✗", fontsize=22, ha="center", va="center",
                        color="#F85149", fontweight="bold", zorder=3
                    )
                else:
                    # Порожнє коло — немає даних
                    circle = plt.Circle(
                        (di * (CELL + GAP) + CELL / 2, -(hi * 1.6) + 0.3),
                        0.44, color="#1C2128", alpha=1.0, zorder=2
                    )
                    ax_h.add_patch(circle)

            # Підпис звички ліворуч
            ax_h.text(-2.2, -(hi * 1.6) + 0.3, HABIT_LABELS[hkey],
                      fontsize=26, color=TEXT, va="center", ha="right",
                      fontweight="bold")

        total_w = len(all_dates) * (CELL + GAP)
        ax_h.set_xlim(-5.0, total_w + 0.5)  # більше місця зліва для emoji
        ax_h.set_ylim(-len(HABITS) * 1.6 - 0.4, 1.8)

        # Місячні мітки по X
        cur = start_date.replace(day=1)
        while cur <= end_date:
            day_offset = (cur - start_date).days
            x_pos = day_offset * (CELL + GAP)
            ax_h.text(x_pos, 1.5, f"{UA_MONTHS[cur.month]} {cur.year}",
                      fontsize=26, color=MUTED, ha="left", fontweight="bold")
            # Вертикальна лінія-роздільник між місяцями
            if cur != start_date:
                ax_h.axvline(x=x_pos - GAP / 2, ymin=0.02, ymax=0.92,
                             color=BORDER, linewidth=3.0, alpha=0.5)
            nxt_month = cur.month % 12 + 1
            nxt_year  = cur.year + (1 if cur.month == 12 else 0)
            cur = cur.replace(year=nxt_year, month=nxt_month, day=1)

        ax_h.set_title("Звички з 1 січня 2026",
                       color=TEXT, fontsize=30, fontweight="bold", pad=20)

        # ── BOTTOM: вага за 6 місяців ─────────────────────────────────────────
        ax_w = fig.add_subplot(gs[1])
        ax_w.set_facecolor(PANEL)

        present_w = [(d, wdata.get(d.isoformat()))
                     for d in all_dates if wdata.get(d.isoformat()) is not None]

        if len(present_w) >= 2:
            import matplotlib.dates as _mdates
            xd = [d for d, _ in present_w]
            yi = np.array([v for _, v in present_w])

            ax_w.axhspan(77.0, 79.0, alpha=0.08, color=BLUE)
            ax_w.fill_between(xd, yi, min(yi) - 0.5, alpha=0.15, color=GREEN)
            ax_w.plot(xd, yi, color=GREEN, linewidth=4.0, zorder=4, label="вага")
            ax_w.scatter(xd, yi, color=GREEN, s=54, zorder=5, alpha=0.7)

            # Тренд
            xn = np.arange(len(yi))
            z  = np.polyfit(xn, yi, 1)
            p  = np.poly1d(z)
            trend_color = RED if z[0] > 0.01 else (GREEN if z[0] < -0.01 else MUTED)
            ax_w.plot(xd, p(xn), color=trend_color,
                      linewidth=4.0, linestyle=":", zorder=3, label="тренд")

            # Мін/макс
            best_i = int(np.argmin(yi))
            ax_w.scatter([xd[best_i]], [yi[best_i]], color=YELLOW, s=240,
                         zorder=7, marker="D")
            ax_w.annotate(f"мін {yi[best_i]:.1f}",
                          xy=(xd[best_i], yi[best_i]),
                          xytext=(6, -16), textcoords="offset points",
                          color=YELLOW, fontsize=22, fontweight="bold")

            ax_w.axhline(78.0, color=BLUE, linewidth=3.0, linestyle="--",
                         alpha=0.7, label="ціль 78 кг")

            ax_w.set_xlim(xd[0], xd[-1])
            ax_w.set_ylim(min(yi) - 2, max(yi) + 2)
            ax_w.xaxis.set_major_formatter(_mdates.DateFormatter("%d.%m"))
            ax_w.xaxis.set_major_locator(_mdates.WeekdayLocator(interval=2))
            plt.setp(ax_w.xaxis.get_majorticklabels(), rotation=45, ha="right")

            ax_w.legend(fontsize=24, loc="upper right",
                        framealpha=0.3, facecolor=PANEL,
                        edgecolor=BORDER, labelcolor=TEXT)

            diff = float(yi[-1] - yi[0])
            sign = "+" if diff > 0 else ""
            slope6 = z[0] * 180
            ax_w.text(0.01, 0.94,
                      f"Старт: {yi[0]:.1f}  →  Зараз: {yi[-1]:.1f} кг  ({sign}{diff:.1f})   |   тренд за 6 міс: {'+' if slope6>0 else ''}{slope6:.1f} кг",
                      transform=ax_w.transAxes, fontsize=24,
                      color=trend_color, fontweight="bold", va="top")
        else:
            ax_w.text(0.5, 0.5, "Недостатньо даних",
                      ha="center", va="center", color=MUTED, fontsize=24,
                      transform=ax_w.transAxes)

        ax_w.set_title("Вага з 1 січня 2026",
                       color=TEXT, fontsize=26, fontweight="bold")
        ax_w.tick_params(colors=MUTED, labelsize=24)
        for spine in ax_w.spines.values():
            spine.set_edgecolor(BORDER)
        ax_w.grid(axis="y", alpha=0.2)

        end_label = today.strftime("%d.%m.%Y")
        fig.suptitle(
            f"Дашборд 2026  —  до {end_label}",
            fontsize=28, color=TEXT, fontweight="bold", y=1.01
        )

        raw_bytes = _buf(fig)

        # ── PIL: накласти emoji на підписи звичок ──────────────────────────
        try:
            from PIL import Image, ImageDraw, ImageFont as _IFont
            import io as _io2

            _EMOJI_FONT_PATH = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
            if os.path.exists(_EMOJI_FONT_PATH):
                _pil_img = Image.open(_io2.BytesIO(raw_bytes)).convert("RGBA")
                _iw, _ih = _pil_img.size
                # NotoColorEmoji підтримує тільки розмір 109
                _em_font = _IFont.truetype(_EMOJI_FONT_PATH, 109)
                # Цільовий розмір emoji ~ 4% висоти зображення
                _target_sz = max(30, int(_ih * 0.04))

                _habits_order = ["shower", "run", "water", "tea", "sauna"]
                _emoji_map = HABIT_EMOJIS
                _heatmap_top    = 0.08
                _heatmap_bottom = 0.60
                _heatmap_h = _heatmap_bottom - _heatmap_top
                _row_h = _heatmap_h / len(_habits_order)

                for _hi, _hk in enumerate(_habits_order):
                    _em = _emoji_map.get(_hk, "")
                    if not _em:
                        continue
                    # Малюємо emoji в тимчасове зображення
                    _tmp = Image.new("RGBA", (130, 130), (0, 0, 0, 0))
                    _tmp_draw = ImageDraw.Draw(_tmp)
                    _tmp_draw.text((5, 5), _em, font=_em_font, embedded_color=True)
                    # Масштабуємо до target_sz
                    _tmp = _tmp.resize((_target_sz, _target_sz), Image.LANCZOS)
                    # y — центр ряду
                    _y_rel = _heatmap_top + _row_h * _hi + _row_h * 0.35
                    _y_px = int(_y_rel * _ih) - _target_sz // 2
                    # x — ставимо emoji В КІНЕЦЬ підписів (після тексту)
                    # Підписи закінчуються приблизно на 8.5% від лівого краю
                    # Emoji ставимо на самому початку підпису (~6.5% від краю)
                    _label_end_x = int(_iw * 0.085)
                    _x_px = _label_end_x - _target_sz - 4
                    _pil_img.paste(_tmp, (_x_px, _y_px), _tmp)

                _out = _io2.BytesIO()
                _pil_img.convert("RGB").save(_out, format="PNG")
                raw_bytes = _out.getvalue()
        except Exception as _pil_e:
            print(f"[charts] PIL emoji overlay error: {_pil_e}")

        return raw_bytes
    except Exception as e:
        import traceback
        print(f"[charts] monthly_dashboard error: {e}\n{traceback.format_exc()}")
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

        fig, ax = plt.subplots(figsize=(20, 10), facecolor=BG)
        ax.set_facecolor(PANEL)

        # Тренд лінія
        z = np.polyfit(xi, yi, 1)
        p = np.poly1d(z)
        trend_y = [p(x) for x in xi]
        trend_color = RED if z[0] > 0.05 else (GREEN if z[0] < -0.05 else MUTED)

        ax.fill_between(xi, yi, min(yi) - 0.5, alpha=0.15, color=GREEN)
        ax.plot(xi, yi, color=GREEN, linewidth=4.0, zorder=4, label="Вага")
        ax.plot(xi, trend_y, color=trend_color, linewidth=3.0,
                linestyle="--", alpha=0.8, zorder=3, label="Тренд")
        ax.scatter([xi[-1]], [yi[-1]], color=GREEN, s=210, zorder=5)
        ax.axhline(78.0, color=BLUE, linewidth=3.0, linestyle=":", alpha=0.6)

        tick_step = max(1, len(xi) // 7)
        ax.set_xticks(xi[::tick_step])
        ax.set_xticklabels(xlabels[::tick_step], fontsize=20, color=MUTED)
        ax.tick_params(colors=MUTED)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER)
        ax.grid(axis="y", alpha=0.2)
        ax.legend(fontsize=20, framealpha=0.3, facecolor=PANEL,
                  edgecolor=BORDER, labelcolor=TEXT)

        diff = yi[-1] - yi[0]
        sign = "+" if diff > 0 else ""
        ax.set_title(f"Вага  {sign}{diff:.1f} кг за {len(present)} днів  ·  Зараз: {yi[-1]:.1f} кг",
                     color=TEXT, fontsize=24, fontweight="bold")

        fig.tight_layout(pad=1.2)
        return _buf(fig)
    except Exception as e:
        print(f"[charts] weight_anomaly error: {e}")
        return None

# ── 6. МІНІ-ДАШБОРД (для кожного 30-хв звіту) ────────────────────────────────

def plot_mini_dashboard(today_str: str = None) -> bytes | None:
    """
    Компактний графік для кожного 30-хв звіту:
    - Ліво: вага за 14 днів + лінія тренду + зона цілі
    - Право: звички сьогодні (горизонтальні бари з % тижня)
    Розмір ~800×320px, легкий і швидкий.
    """
    if not HAS_MPL:
        return None
    try:
        _rc()
        raw    = _load_habits()
        wdata  = _load_weight()

        if today_str is None:
            today_str = date.today().isoformat()

        today_d   = datetime.strptime(today_str, "%Y-%m-%d").date()
        HABITS    = ["shower", "run", "water", "tea", "sauna"]
        entry     = raw.get(today_str, {}) or {}

        fig, (ax_w, ax_h) = plt.subplots(1, 2, figsize=(24, 12),
                                          gridspec_kw={"width_ratios": [1.6, 1]},
                                          facecolor=BG)

        # ── Ліво: вага 14 днів ─────────────────────────────────────────────────
        ax_w.set_facecolor(PANEL)
        w_dates = [today_d - timedelta(days=i) for i in range(13, -1, -1)]
        w_vals  = [wdata.get(d.isoformat()) for d in w_dates]
        present = [(i, v) for i, v in enumerate(w_vals) if v is not None]

        if len(present) >= 2:
            xi = np.array([p[0] for p in present])
            yi = np.array([p[1] for p in present])

            # Зона цілі
            ax_w.axhspan(77.0, 79.0, alpha=0.10, color=BLUE, zorder=0)

            # Fill + лінія
            ax_w.fill_between(xi, yi, min(yi) - 0.3, alpha=0.20, color=GREEN, zorder=1)
            ax_w.plot(xi, yi, color=GREEN, linewidth=4.0, zorder=4)
            ax_w.scatter(xi, yi, color=GREEN, s=66, alpha=0.6, zorder=5)
            ax_w.scatter([xi[-1]], [yi[-1]], color=GREEN, s=210, zorder=6)

            # Ціль
            ax_w.axhline(78.0, color=BLUE, linewidth=3.0, linestyle="--", alpha=0.8)

            # Тренд
            z = np.polyfit(xi, yi, 1)
            p = np.poly1d(z)
            ax_w.plot([xi[0], xi[-1]], [p(xi[0]), p(xi[-1])],
                      color=RED if z[0] > 0.02 else (GREEN if z[0] < -0.02 else MUTED),
                      linewidth=3.0, linestyle=":", zorder=3)

            slope_week = z[0] * 7
            sign = "+" if slope_week > 0 else ""
            t_col = RED if z[0] > 0.02 else (GREEN if z[0] < -0.02 else MUTED)

            ax_w.set_ylim(min(yi) - 1.2, max(yi) + 1.5)

            # Підпис поточного значення
            ax_w.annotate(f"{yi[-1]:.1f} кг",
                          xy=(xi[-1], yi[-1]),
                          xytext=(-50, 12), textcoords="offset points",
                          color=GREEN, fontsize=26, fontweight="bold")

            # Тренд підпис
            ax_w.text(0.03, 0.96,
                      f"тренд: {sign}{slope_week:.2f} кг/тижд",
                      transform=ax_w.transAxes,
                      fontsize=24, color=t_col, va="top", fontweight="bold")

            # X-тіки
            tick_ix = [0, 7, 13]
            ax_w.set_xticks(tick_ix)
            ax_w.set_xticklabels(
                [w_dates[t].strftime("%d.%m") for t in tick_ix],
                fontsize=24, color=MUTED
            )
        else:
            ax_w.text(0.5, 0.5, "Немає даних", ha="center", va="center",
                      color=MUTED, fontsize=26, transform=ax_w.transAxes)

        ax_w.set_title("Вага + тренд  (14 днів)", color=TEXT, fontsize=26, fontweight="bold", pad=8)
        for spine in ax_w.spines.values():
            spine.set_edgecolor(BORDER)
        ax_w.tick_params(colors=MUTED, labelsize=24)
        ax_w.grid(axis="y", alpha=0.18)

        # ── Право: звички + % тижня ────────────────────────────────────────────
        ax_h.set_facecolor(PANEL)
        week_dates = [today_d - timedelta(days=i) for i in range(6, -1, -1)]

        y_pos    = list(range(len(HABITS)))
        bar_vals = []
        bar_cols = []
        week_pct = []

        for hkey in HABITS:
            done_today = entry.get(hkey) is True
            bar_vals.append(1.0 if done_today else 0.10)
            bar_cols.append(HABIT_COLORS[hkey] if done_today else "#21262D")
            wd = sum(1 for d in week_dates
                     if (raw.get(d.isoformat(), {}) or {}).get(hkey) is True)
            week_pct.append(wd / 7)

        # Фонова смуга 7-денного %
        ax_h.barh(y_pos, week_pct, height=0.6, color=[HABIT_COLORS[h] for h in HABITS],
                  alpha=0.22, zorder=2)
        # Бар сьогодні
        ax_h.barh(y_pos, bar_vals, height=0.6, color=bar_cols, zorder=3)

        ax_h.set_xlim(0, 1.35)
        ax_h.set_yticks(y_pos)
        ax_h.set_yticklabels([HABIT_LABELS[h] for h in HABITS], fontsize=24, color=TEXT)
        ax_h.set_xticks([])
        ax_h.invert_yaxis()
        ax_h.grid(False)
        for spine in ax_h.spines.values():
            spine.set_visible(False)

        # Емодзі + % тижня праворуч
        for i, hkey in enumerate(HABITS):
            icon  = "✓" if entry.get(hkey) is True else "✗"
            pct_v = int(week_pct[i] * 100)
            ax_h.text(1.04, i, f"{icon} {pct_v}%", va="center", ha="left",
                      fontsize=24, color=TEXT)

        done_today = sum(1 for h in HABITS if entry.get(h) is True)
        ax_h.set_title(f"Звички  {done_today}/{len(HABITS)} сьогодні", color=TEXT,
                       fontsize=26, fontweight="bold", pad=8)

        # ── Загальний заголовок ─────────────────────────────────────────────────
        fig.suptitle(
            f"Міні-дашборд  {today_str[8:]}.{today_str[5:7]}",
            fontsize=26, color=TEXT, fontweight="bold", y=1.02
        )

        fig.tight_layout(pad=1.5)
        return _buf(fig)
    except Exception as e:
        print(f"[charts] mini_dashboard error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 6. COMBINED DASHBOARD — одна велика картинка: звички + вага + біг + фінанси
# ═══════════════════════════════════════════════════════════════════════════════

def plot_combined_dashboard() -> bytes | None:
    """
    Одна велика картинка (1800x2200):
    ┌─────────────────────────────────────────────────┐
    │  ROW 1: Heatmap звичок з 1 січня 2026 (повна ш) │
    ├──────────────────┬──────────────────────────────┤
    │  ROW 2L: Вага    │  ROW 2R: Місячні надходження │
    │  з 1 січня 2026  │  (зарплата Minebea + інше)   │
    ├──────────────────┴──────────────────────────────┤
    │  ROW 3: Бігові км по тижнях (поточний місяць)   │
    └─────────────────────────────────────────────────┘
    """
    if not HAS_MPL:
        return None
    try:
        _rc()
        import calendar as _cal
        import matplotlib.gridspec as _gs
        import matplotlib.dates as _mdates
        import matplotlib.patches as _mpatch
        from datetime import date as _date_cls

        now = datetime.now(timezone.utc) + timedelta(hours=2)
        today = now.date()
        start_date = _date_cls(2026, 1, 1)
        all_dates = [start_date + timedelta(days=i)
                     for i in range((today - start_date).days + 1)]

        raw = _load_habits()
        wdata = _load_weight()
        HABITS = ["shower", "run", "water", "tea", "sauna", "spray"]

        UA_MONTHS = {1:"Січ",2:"Лют",3:"Бер",4:"Кві",5:"Тра",6:"Чер",
                     7:"Лип",8:"Сер",9:"Вер",10:"Жов",11:"Лис",12:"Гру"}

        # ── Фігура ────────────────────────────────────────────────────────────
        n_days = len(all_dates)
        fig_w  = max(40, min(n_days * 0.15, 80))
        fig    = plt.figure(figsize=(fig_w, 34), facecolor=BG)
        outer_gs = _gs.GridSpec(3, 1, figure=fig,
                                hspace=0.70,
                                height_ratios=[3.0, 2.5, 2.5],
                                left=0.07, right=0.98,
                                top=0.915, bottom=0.04)

        # ══════════════════════════════════════════════════════════════════════
        # ROW 1: Heatmap звичок
        # ══════════════════════════════════════════════════════════════════════
        ax_h = fig.add_subplot(outer_gs[0])
        ax_h.set_facecolor(BG)
        ax_h.axis("off")

        CELL = 1.4
        GAP  = 0.25

        for hi, hkey in enumerate(HABITS):
            color = HABIT_COLORS.get(hkey, MUTED)
            for di, d in enumerate(all_dates):
                entry = raw.get(d.isoformat(), {}) or {}
                v = entry.get(hkey)
                cx = di * (CELL + GAP) + CELL / 2
                cy = -(hi * 1.7) + 0.3
                if v is True:
                    circ = plt.Circle((cx, cy), 0.58, color=color, alpha=0.9, zorder=2)
                    ax_h.add_patch(circ)
                    ax_h.text(cx, cy, "✓", fontsize=26, ha="center", va="center",
                              color="white", fontweight="bold", zorder=3)
                elif v is False:
                    circ = plt.Circle((cx, cy), 0.58, color="#21262D", alpha=1.0, zorder=2)
                    ax_h.add_patch(circ)
                    ax_h.text(cx, cy, "✗", fontsize=26, ha="center", va="center",
                              color=RED, fontweight="bold", zorder=3)
                else:
                    circ = plt.Circle((cx, cy), 0.58, color="#1C2128", alpha=1.0, zorder=2)
                    ax_h.add_patch(circ)

            # Підпис зліва
            ax_h.text(-3.0, -(hi * 1.7) + 0.3,
                      HABIT_LABELS.get(hkey, hkey),
                      fontsize=30, color=TEXT, va="center", ha="right",
                      fontweight="bold")

        total_w_heat = len(all_dates) * (CELL + GAP)
        ax_h.set_xlim(-8.0, total_w_heat + 1.5)
        ax_h.set_ylim(-len(HABITS) * 1.7 - 0.5, 2.5)

        # Місячні мітки
        cur = start_date.replace(day=1)
        while cur <= today:
            day_off = (cur - start_date).days
            xp = day_off * (CELL + GAP)
            ax_h.text(xp, 2.2, f"{UA_MONTHS[cur.month]} {cur.year}",
                      fontsize=28, color=MUTED, ha="left", fontweight="bold")
            if cur != start_date:
                ax_h.axvline(x=xp - GAP / 2, ymin=0.02, ymax=0.93,
                             color=BORDER, linewidth=3.0, alpha=0.6)
            nxt_m = cur.month % 12 + 1
            nxt_y = cur.year + (1 if cur.month == 12 else 0)
            cur = cur.replace(year=nxt_y, month=nxt_m, day=1)

        # Статистика по кожній звичці
        for hi, hkey in enumerate(HABITS):
            done_cnt = sum(
                1 for d in all_dates
                if (raw.get(d.isoformat()) or {}).get(hkey) is True
            )
            pct = done_cnt * 100 // max(len(all_dates), 1)
            ax_h.text(total_w_heat + 1.5, -(hi * 1.7) + 0.3,
                      f"{pct}%", fontsize=28, color=MUTED,
                      va="center", ha="left")

        ax_h.set_title("Звички з 1 січня 2026",
                       color=TEXT, fontsize=34, fontweight="bold", pad=58)

        # ══════════════════════════════════════════════════════════════════════
        # ROW 2: Вага (на всю ширину)
        # ══════════════════════════════════════════════════════════════════════
        ax_w = fig.add_subplot(outer_gs[1])
        ax_w.set_facecolor(PANEL)

        present_w = [(d, wdata.get(d.isoformat()))
                     for d in all_dates if wdata.get(d.isoformat()) is not None]

        if len(present_w) >= 2:
            xd = [d for d, _ in present_w]
            yi = np.array([v for _, v in present_w])

            ax_w.axhspan(77.0, 79.0, alpha=0.08, color=BLUE)
            ax_w.fill_between(xd, yi, min(yi) - 0.5, alpha=0.15, color=GREEN)
            ax_w.plot(xd, yi, color=GREEN, linewidth=4.0, zorder=4)
            ax_w.scatter(xd, yi, color=GREEN, s=60, zorder=5, alpha=0.7)

            xn = np.arange(len(yi))
            z  = np.polyfit(xn, yi, 1)
            p  = np.poly1d(z)
            tc = RED if z[0] > 0.01 else (GREEN if z[0] < -0.01 else MUTED)
            ax_w.plot(xd, p(xn), color=tc, linewidth=4.0, linestyle=":", zorder=3,
                      label="тренд")

            best_i = int(np.argmin(yi))
            ax_w.scatter([xd[best_i]], [yi[best_i]], color=YELLOW, s=100,
                         zorder=7, marker="D")
            ax_w.annotate(f"мін {yi[best_i]:.1f}",
                          xy=(xd[best_i], yi[best_i]),
                          xytext=(6, -18), textcoords="offset points",
                          color=YELLOW, fontsize=26, fontweight="bold")

            ax_w.axhline(78.0, color=BLUE, linewidth=3.0, linestyle="--",
                         alpha=0.7, label="ціль 78 кг")

            ax_w.set_xlim(xd[0], xd[-1])
            ax_w.set_ylim(min(yi) - 2, max(yi) + 2)
            ax_w.xaxis.set_major_formatter(_mdates.DateFormatter("%d.%m"))
            ax_w.xaxis.set_major_locator(_mdates.WeekdayLocator(interval=1))
            plt.setp(ax_w.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=26)
            ax_w.tick_params(colors=MUTED, labelsize=26)

            ax_w.legend(fontsize=26, loc="upper right",
                        framealpha=0.3, facecolor=PANEL,
                        edgecolor=BORDER, labelcolor=TEXT)

            diff = float(yi[-1] - yi[0])
            sign = "+" if diff > 0 else ""
            ax_w.text(0.01, 0.97,
                      f"Старт: {yi[0]:.1f}  →  Зараз: {yi[-1]:.1f} кг  ({sign}{diff:.1f} кг)",
                      transform=ax_w.transAxes, fontsize=28,
                      color=tc, fontweight="bold", va="top")
        else:
            ax_w.text(0.5, 0.5, "Немає даних ваги",
                      ha="center", va="center", color=MUTED, fontsize=28,
                      transform=ax_w.transAxes)

        ax_w.set_title("Вага з 1 січня 2026",
                       color=TEXT, fontsize=34, fontweight="bold")
        for spine in ax_w.spines.values():
            spine.set_edgecolor(BORDER)
        ax_w.grid(axis="y", alpha=0.2)

        # ══════════════════════════════════════════════════════════════════════
        # ROW 3: Бігові км по місяцях + поточний місяць по тижнях
        # ══════════════════════════════════════════════════════════════════════
        inner_gs3 = _gs.GridSpecFromSubplotSpec(
            1, 2, subplot_spec=outer_gs[2], wspace=0.35,
            width_ratios=[1.0, 1.5]
        )

        ax_run_m = fig.add_subplot(inner_gs3[0])  # км по місяцях
        ax_run_d = fig.add_subplot(inner_gs3[1])  # км по днях поточного місяця

        ax_run_m.set_facecolor(PANEL)
        ax_run_d.set_facecolor(PANEL)

        try:
            import sys as _sys_r; _sys_r.path.insert(0, _DIR)
            from strava import get_month_stats as _gms_r

            # Місяці з 2026-01 до поточного
            _run_months = []
            _cm2 = _date_cls(2026, 1, 1)
            while _cm2 <= today:
                _run_months.append((_cm2.year, _cm2.month))
                nxm3 = _cm2.month % 12 + 1
                nxy3 = _cm2.year + (1 if _cm2.month == 12 else 0)
                _cm2 = _cm2.replace(year=nxy3, month=nxm3, day=1)

            _run_km = []
            _run_labels_m = []
            for _ry, _rm in _run_months:
                _ms = _gms_r(_ry, _rm)
                _run_km.append(_ms.get("km", 0) or 0)
                _run_labels_m.append(UA_MONTHS[_rm])

            _xr = np.arange(len(_run_months))
            _colors_r = [ORANGE if km > 0 else "#21262D" for km in _run_km]
            _bars_r = ax_run_m.bar(_xr, _run_km, color=_colors_r, width=0.65, zorder=2)

            for bar, km in zip(_bars_r, _run_km):
                if km > 0:
                    ax_run_m.text(bar.get_x() + bar.get_width()/2,
                                  bar.get_height() + 0.3,
                                  f"{km:.0f}", ha="center", va="bottom",
                                  fontsize=28, color=ORANGE, fontweight="bold")

            # Ціль 40 км/місяць
            ax_run_m.axhline(40, color=GREEN, linewidth=3.0, linestyle="--",
                             alpha=0.7, label="ціль 40 км")
            ax_run_m.set_xticks(_xr)
            ax_run_m.set_xticklabels(_run_labels_m, fontsize=28, color=TEXT)
            ax_run_m.tick_params(colors=MUTED, labelsize=26)
            ax_run_m.legend(fontsize=26, framealpha=0.3, facecolor=PANEL,
                            edgecolor=BORDER, labelcolor=TEXT)
            ax_run_m.grid(axis="y", alpha=0.2)
            ax_run_m.set_title("Км по місяцях 2026",
                               color=TEXT, fontsize=32, fontweight="bold")
            for spine in ax_run_m.spines.values():
                spine.set_edgecolor(BORDER)

            # Поточний місяць по днях
            _cur_ms = _gms_r(today.year, today.month)
            _runs_list = _cur_ms.get("runs_list", [])
            _days_in_m = _cal.monthrange(today.year, today.month)[1]
            _km_by_day = {d: 0.0 for d in range(1, _days_in_m + 1)}
            for _run in _runs_list:
                _km_by_day[_run["date"].day] += _run["dist_km"]

            _dd = list(range(1, _days_in_m + 1))
            _kk = [_km_by_day[d] for d in _dd]
            _cc = [GREEN if k > 0 else "#21262D" for k in _kk]
            _bars_d = ax_run_d.bar(_dd, _kk, color=_cc, width=0.7, zorder=2)

            for bar, km in zip(_bars_d, _kk):
                if km > 0:
                    ax_run_d.text(bar.get_x() + bar.get_width()/2,
                                  bar.get_height() + 0.05,
                                  f"{km:.1f}", ha="center", va="bottom",
                                  fontsize=26, color=GREEN, fontweight="bold")

            # Сьогодні — вертикальна мітка
            ax_run_d.axvline(today.day, color=YELLOW, linewidth=3.0,
                             linestyle="--", alpha=0.7, label="сьогодні")

            _month_names_ua = ["", "Січень", "Лютий", "Березень", "Квітень",
                               "Травень", "Червень", "Липень", "Серпень",
                               "Вересень", "Жовтень", "Листопад", "Грудень"]
            _total_km_m = _cur_ms.get("km", 0) or 0
            _runs_cnt   = _cur_ms.get("runs", 0) or 0
            ax_run_d.set_title(
                f"{_month_names_ua[today.month]} {today.year}  —  {_runs_cnt} пробіжок  /  {_total_km_m} км",
                color=TEXT, fontsize=32, fontweight="bold"
            )
            ax_run_d.set_xlim(0.5, _days_in_m + 0.5)
            ax_run_d.set_xticks([1, 5, 10, 15, 20, 25, _days_in_m])
            ax_run_d.tick_params(colors=MUTED, labelsize=26)
            ax_run_d.grid(axis="y", alpha=0.2)
            ax_run_d.legend(fontsize=26, framealpha=0.3, facecolor=PANEL,
                            edgecolor=BORDER, labelcolor=TEXT)
            for spine in ax_run_d.spines.values():
                spine.set_edgecolor(BORDER)

        except Exception as _e_run:
            ax_run_m.text(0.5, 0.5, f"Strava: {_e_run}", ha="center", va="center",
                          color=MUTED, fontsize=26, transform=ax_run_m.transAxes)
            ax_run_d.text(0.5, 0.5, "Немає даних",
                          ha="center", va="center", color=MUTED, fontsize=26,
                          transform=ax_run_d.transAxes)

        # ── Загальний заголовок ──────────────────────────────────────────────
        fig.suptitle(
            f"Дашборд 2026  —  до {today.strftime('%d.%m.%Y')}",
            fontsize=40, color=TEXT, fontweight="bold", y=0.985
        )

        # ── Зберігаємо ────────────────────────────────────────────────────────
        # БЕЗ PIL-overlay емодзі — він налазив і давав квадрати.
        # Назви звичок = чистий текст, ідентифікація по кольору кружечків.
        raw_bytes = _buf(fig)
        return raw_bytes

    except Exception as e:
        import traceback
        print(f"[charts] combined_dashboard error: {e}\n{traceback.format_exc()}")
        return None
