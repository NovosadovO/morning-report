#!/usr/bin/env python3
"""
Daily Morning Market Report — CI version (GitHub Actions)
Uses: Resend (email), Cloudinary (image hosting), CoinGecko + yfinance (data)
"""

import json, datetime, os, sys, time, base64, hashlib, hmac
import urllib.request, urllib.error, urllib.parse

RECIPIENT          = os.environ.get('RECIPIENT', 'novosadovoleg@gmail.com')
RESEND_API_KEY     = os.environ['RESEND_API_KEY']
CLOUDINARY_CLOUD   = os.environ['CLOUDINARY_CLOUD_NAME']
CLOUDINARY_KEY     = os.environ['CLOUDINARY_API_KEY']
CLOUDINARY_SECRET  = os.environ['CLOUDINARY_API_SECRET']

CHARTS_DIR = '/tmp/charts'
os.makedirs(CHARTS_DIR, exist_ok=True)

def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ── FORMATTERS ───────────────────────────────────────────────────────────

def fmt_price(p, decimals=2):
    if p is None: return "—"
    if p >= 1000: return f"${p:,.2f}"
    if p >= 1:    return f"${p:.4f}" if p < 10 else f"${p:.2f}"
    return f"${p:.6f}"

def fmt_pct(p):
    if p is None: return "—"
    return f"{p:+.2f}%"

def fmt_mcap(v):
    if v is None: return "—"
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.2f}B"
    if v >= 1e6:  return f"${v/1e6:.2f}M"
    return f"${v:.0f}"

def hchg(v):
    try: return '#0e9f6e' if float(str(v).replace('+','')) >= 0 else '#e02424'
    except: return '#374151'

# ── DATA FETCHING ────────────────────────────────────────────────────────

def fetch_crypto_top20():
    url = ("https://api.coingecko.com/api/v3/coins/markets"
           "?vs_currency=usd&order=market_cap_desc&per_page=20&page=1"
           "&sparkline=false&price_change_percentage=24h")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            break
        except Exception as e:
            if attempt < 2: time.sleep(15)
            else: raise
    return [{
        'rank':   i+1,
        'ticker': c['symbol'].upper(),
        'name':   c['name'],
        'price':  c['current_price'],
        'change': c.get('price_change_percentage_24h'),
        'mcap':   c.get('market_cap'),
        'volume': c.get('total_volume'),
    } for i, c in enumerate(data)]

def fetch_vava():
    try:
        time.sleep(3)
        url = "https://api.coingecko.com/api/v3/simple/price?ids=avalanche-2&vs_currencies=eur&include_24hr_change=true"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        return {
            'ticker': 'VAVA', 'name': 'VanEck Avalanche ETN (Xetra)',
            'price': d['avalanche-2']['eur'],
            'change_pct': d['avalanche-2'].get('eur_24h_change'),
            'currency': 'EUR', 'note': 'approx. NAV = AVAX/EUR'
        }
    except:
        return {'ticker':'VAVA','name':'VanEck Avalanche ETN','price':None,'change_pct':None,'currency':'EUR','note':''}

def fetch_yf(symbols):
    """Fetch prices directly from Yahoo Finance JSON API — no library needed."""
    result = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Origin': 'https://finance.yahoo.com',
        'Referer': 'https://finance.yahoo.com/',
    }
    for sym in symbols:
        price = chg_pct = mcap = None
        for attempt in range(3):
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as r:
                    d = json.loads(r.read())
                meta   = d['chart']['result'][0]['meta']
                closes = d['chart']['result'][0]['indicators']['quote'][0]['close']
                closes = [c for c in closes if c is not None]
                if len(closes) >= 2:
                    price   = closes[-1]
                    chg_pct = (closes[-1] - closes[-2]) / closes[-2] * 100
                elif len(closes) == 1:
                    price = closes[-1]
                mcap = meta.get('marketCap')
                break
            except Exception:
                if attempt < 2:
                    time.sleep(5 + attempt * 3)
        result[sym] = {'price': price, 'change_pct': chg_pct,
                       'prev_close': None, 'market_cap': mcap}
        time.sleep(0.4)
    return result

STOCK_SYMBOLS  = ['NVDA','AAPL','MSFT','AMZN','META','GOOG','TSLA','AVGO','JPM','BRK-B']
STOCK_NAMES    = {'NVDA':'NVIDIA','AAPL':'Apple','MSFT':'Microsoft','AMZN':'Amazon',
                  'META':'Meta','GOOG':'Alphabet','TSLA':'Tesla','AVGO':'Broadcom',
                  'JPM':'JPMorgan','BRK-B':'Berkshire'}
ETF_SYMBOLS    = ['IBIT','ETHA','GAVA','BAVA']
ETF_NAMES      = {'IBIT':'BlackRock Bitcoin ETF','ETHA':'BlackRock Ethereum ETF',
                  'GAVA':'Grayscale Avalanche ETF','BAVA':'Bitwise Avalanche ETF'}
TOP_ETF_SYMBOLS = ['SPY','QQQ','GLD','TLT','VTI','IWM','EEM','HYG','VNQ','ARKK']
TOP_ETF_NAMES   = {'SPY':'S&P 500 ETF','QQQ':'NASDAQ-100 ETF','GLD':'Gold ETF',
                   'TLT':'20+Y Treasury','VTI':'Total Market ETF','IWM':'Russell 2000',
                   'EEM':'Emerging Markets','HYG':'High Yield Bond','VNQ':'Real Estate ETF',
                   'ARKK':'ARK Innovation'}
INDEX_SYMBOLS  = ['^GSPC','^NDX','^DJI','^VIX']
INDEX_NAMES    = {'^GSPC':'S&P 500','^NDX':'NASDAQ 100','^DJI':'Dow Jones','^VIX':'VIX'}

# ── CHARTS ───────────────────────────────────────────────────────────────

def generate_charts(crypto20, stocks_data, etf_data, top_etf_data, indices_data):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    BG = '#f5f7fa'; PANEL = '#ffffff'; ACCENT = '#1a56db'
    POS = '#0e9f6e'; NEG = '#e02424'; ZERO = '#9ca3af'
    GRID_C = '#e5e7eb'; TEXT = '#111827'; SUBTEXT = '#6b7280'

    def style_ax(ax, fig):
        fig.patch.set_facecolor(BG); ax.set_facecolor(PANEL)
        ax.tick_params(colors=TEXT, labelsize=9)
        for sp in ax.spines.values():
            sp.set_color(GRID_C); sp.set_linewidth(0.8)

    def add_title(ax, title):
        ax.set_title(title, color=TEXT, fontsize=13, fontweight='bold', pad=14, loc='left')

    def crypto_lollipop(labels, values, title, path):
        fig, ax = plt.subplots(figsize=(11, 9))
        style_ax(ax, fig)
        y = np.arange(len(labels))
        colors = [POS if v >= 0 else NEG for v in values]
        for yi, (val, col) in enumerate(zip(values, colors)):
            ax.plot([0, val], [yi, yi], color=col, alpha=0.35, linewidth=2)
            ax.scatter([val], [yi], color=col, s=80, zorder=5, edgecolors=PANEL, linewidth=0.8)
        ax.axvline(0, color=ZERO, linewidth=1.2, linestyle='--', alpha=0.6)
        ax.set_yticks(y); ax.set_yticklabels(labels, color=TEXT, fontsize=10, fontfamily='monospace')
        ax.xaxis.grid(True, color=GRID_C, linewidth=0.5, alpha=0.8); ax.yaxis.grid(False)
        ax.set_xlabel('Зміна за 24г (%)', color=SUBTEXT, fontsize=9)
        add_title(ax, title)
        for yi, (val, col) in enumerate(zip(values, colors)):
            offset = 0.12 if val >= 0 else -0.12
            ax.text(val + offset, yi, f'{val:+.2f}%', va='center',
                    ha='left' if val >= 0 else 'right', color=col, fontsize=8, fontweight='bold')
        for yi in range(0, len(labels), 2):
            ax.axhspan(yi - 0.45, yi + 0.45, color='#000000', alpha=0.03)
        plt.tight_layout(pad=1.5)
        plt.savefig(path, dpi=150, bbox_inches='tight', facecolor=BG); plt.close()

    def gradient_bar_v(labels, values, title, path, figsize=(11, 5)):
        fig, ax = plt.subplots(figsize=figsize)
        style_ax(ax, fig)
        x = np.arange(len(labels)); bar_w = 0.55
        for xi, (val, lbl) in enumerate(zip(values, labels)):
            col = POS if val >= 0 else NEG
            steps = 40
            for s in range(steps):
                frac = s / steps
                alpha = 0.3 + 0.7 * frac if val >= 0 else 0.3 + 0.7 * (1 - frac)
                seg_h = val / steps
                ax.bar(xi, abs(seg_h), bar_w,
                       bottom=(seg_h*s if val>=0 else val + abs(seg_h)*s),
                       color=col, alpha=alpha, linewidth=0)
            ax.bar(xi, val, bar_w, color='none', edgecolor=col, linewidth=1.2, zorder=4)
            pad = max(abs(val) * 0.06, 0.08)
            ax.text(xi, val + (pad if val >= 0 else -pad), f'{val:+.2f}%', ha='center',
                    va='bottom' if val >= 0 else 'top', color=col, fontsize=9, fontweight='bold', zorder=5)
        ax.axhline(0, color=ZERO, linewidth=1.0, linestyle='-', alpha=0.8, zorder=3)
        ax.set_xticks(x); ax.set_xticklabels(labels, color=TEXT, fontsize=9.5)
        ax.yaxis.grid(True, color=GRID_C, linewidth=0.5, alpha=0.7)
        ax.set_ylabel('Зміна (%)', color=SUBTEXT, fontsize=9)
        ax.tick_params(axis='y', colors=SUBTEXT, labelsize=8)
        add_title(ax, title)
        plt.tight_layout(pad=1.5)
        plt.savefig(path, dpi=150, bbox_inches='tight', facecolor=BG); plt.close()

    def index_scorecard(labels, values, prices, title, path):
        n = len(labels)
        fig, axes = plt.subplots(1, n, figsize=(10, 3))
        fig.patch.set_facecolor(BG)
        fig.suptitle(title, color=TEXT, fontsize=13, fontweight='bold', x=0.02, ha='left', y=1.02)
        for i, (ax, lbl, val, price) in enumerate(zip(axes, labels, values, prices)):
            col = POS if val >= 0 else NEG
            ax.set_facecolor(PANEL)
            for sp in ax.spines.values(): sp.set_color(col); sp.set_linewidth(1.5)
            ax.set_xticks([]); ax.set_yticks([])
            ax.text(0.5, 0.82, lbl, ha='center', va='center', color=ACCENT,
                    fontsize=11, fontweight='bold', transform=ax.transAxes)
            ax.text(0.5, 0.52, f"{price:,.2f}" if price else "—", ha='center', va='center',
                    color=TEXT, fontsize=12, fontweight='bold', transform=ax.transAxes)
            arrow = '▲' if val >= 0 else '▼'
            ax.text(0.5, 0.22, f'{arrow} {val:+.2f}%', ha='center', va='center',
                    color=col, fontsize=11, fontweight='bold', transform=ax.transAxes)
        plt.tight_layout(pad=1.0)
        plt.savefig(path, dpi=150, bbox_inches='tight', facecolor=BG); plt.close()

    p1 = f'{CHARTS_DIR}/crypto_top20.png'
    crypto_lollipop([c['ticker'] for c in crypto20],
                    [c['change'] or 0 for c in crypto20], 'Крипто Топ-20 — зміна за 24г', p1)

    p2 = f'{CHARTS_DIR}/stocks_top10.png'
    gradient_bar_v(STOCK_SYMBOLS,
                   [stocks_data.get(s,{}).get('change_pct') or 0 for s in STOCK_SYMBOLS],
                   'Акції США Топ-10', p2, (12, 5))

    p3 = f'{CHARTS_DIR}/crypto_etf.png'
    etf_all  = ETF_SYMBOLS + ['VAVA']
    etf_vals = [etf_data.get(s,{}).get('change_pct') or 0 for s in ETF_SYMBOLS]
    etf_vals += [top_etf_data.get('VAVA',{}).get('change_pct') or 0]
    gradient_bar_v(etf_all, etf_vals, 'Крипто ETF', p3, (10, 4.5))

    p4 = f'{CHARTS_DIR}/top_etf.png'
    gradient_bar_v(TOP_ETF_SYMBOLS,
                   [top_etf_data.get(s,{}).get('change_pct') or 0 for s in TOP_ETF_SYMBOLS],
                   'Топ-10 ETF', p4, (12, 5))

    p5 = f'{CHARTS_DIR}/indices.png'
    index_scorecard([INDEX_NAMES.get(s,s) for s in INDEX_SYMBOLS],
                    [indices_data.get(s,{}).get('change_pct') or 0 for s in INDEX_SYMBOLS],
                    [indices_data.get(s,{}).get('price') or 0 for s in INDEX_SYMBOLS],
                    'Індекси', p5)

    return p1, p2, p3, p4, p5

# ── CLOUDINARY UPLOAD ────────────────────────────────────────────────────

def cloudinary_upload(path):
    timestamp = str(int(time.time()))
    sig_str = f"timestamp={timestamp}{CLOUDINARY_SECRET}"
    signature = hashlib.sha1(sig_str.encode()).hexdigest()

    with open(path, 'rb') as f:
        img_data = f.read()

    boundary = '----FormBoundary' + hashlib.md5(img_data[:16]).hexdigest()
    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'
        f'Content-Type: image/png\r\n\r\n'
    ).encode() + img_data + (
        f'\r\n--{boundary}\r\n'
        f'Content-Disposition: form-data; name="api_key"\r\n\r\n{CLOUDINARY_KEY}'
        f'\r\n--{boundary}\r\n'
        f'Content-Disposition: form-data; name="timestamp"\r\n\r\n{timestamp}'
        f'\r\n--{boundary}\r\n'
        f'Content-Disposition: form-data; name="signature"\r\n\r\n{signature}'
        f'\r\n--{boundary}--\r\n'
    ).encode()

    url = f'https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD}/image/upload'
    req = urllib.request.Request(url, data=body,
          headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read())
    return result['secure_url']

# ── NEWS ─────────────────────────────────────────────────────────────────

def fetch_news():
    """Fetch top-10 crypto/finance news from RSS feeds, translated to Ukrainian"""
    import re
    from email.utils import parsedate_to_datetime

    feeds = [
        ('CoinDesk',      'https://www.coindesk.com/arc/outboundfeeds/rss/'),
        ('CoinTelegraph', 'https://cointelegraph.com/rss'),
        ('Investing.com', 'https://www.investing.com/rss/news.rss'),
    ]

    all_items = []
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)

    for source, url in feeds:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                xml = r.read().decode('utf-8', errors='ignore')
            items = re.findall(r'<item>(.*?)</item>', xml, re.DOTALL)
            for item in items[:15]:
                title_m = re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>', item, re.DOTALL)
                link_m  = re.search(r'<link>(.*?)</link>|<guid[^>]*>(https?://[^<]+)</guid>', item, re.DOTALL)
                date_m  = re.search(r'<pubDate>(.*?)</pubDate>', item)
                title = (title_m.group(1) or title_m.group(2) or '').strip() if title_m else ''
                link  = (link_m.group(1) or link_m.group(2) or '').strip() if link_m else ''
                pub   = date_m.group(1).strip() if date_m else ''
                # parse date
                try:
                    dt = parsedate_to_datetime(pub)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    if dt < cutoff:
                        continue
                except:
                    pass
                if title:
                    all_items.append({'title': title, 'link': link, 'source': source, 'pub': pub})
        except Exception as e:
            log(f"  News fetch error ({source}): {e}")

    # Deduplicate and take top 10
    seen = set()
    unique = []
    for item in all_items:
        key = item['title'][:40].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
        if len(unique) >= 10:
            break

    # Translate to Ukrainian via OpenAI
    OPENAI_KEY = os.environ.get('OPENAI_API_KEY', '')
    if OPENAI_KEY and unique:
        try:
            titles_en = [item['title'] for item in unique]
            prompt = "Translate these news headlines to Ukrainian. Return only a JSON array of translated strings, same order:\n" + json.dumps(titles_en)
            payload = json.dumps({
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3
            }).encode()
            req = urllib.request.Request(
                'https://api.openai.com/v1/chat/completions',
                data=payload,
                headers={
                    'Authorization': f'Bearer {OPENAI_KEY}',
                    'Content-Type': 'application/json'
                }
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                resp = json.loads(r.read())
            content = resp['choices'][0]['message']['content']
            # extract JSON array
            arr_match = re.search(r'\[.*\]', content, re.DOTALL)
            if arr_match:
                translated = json.loads(arr_match.group())
                for i, item in enumerate(unique):
                    if i < len(translated):
                        item['title_uk'] = translated[i]
        except Exception as e:
            log(f"  Translation error: {e}")

    # fallback: use original title
    for item in unique:
        if 'title_uk' not in item:
            item['title_uk'] = item['title']

    return unique


# ── EMAIL VIA RESEND ─────────────────────────────────────────────────────

def send_email(subject, html):
    import requests as req_lib
    payload = {
        "from": "Morning Report <onboarding@resend.dev>",
        "to": [RECIPIENT],
        "subject": subject,
        "html": html
    }
    r = req_lib.post(
        'https://api.resend.com/emails',
        json=payload,
        headers={'Authorization': f'Bearer {RESEND_API_KEY}'},
        timeout=30
    )
    if not r.ok:
        raise Exception(f"Resend error {r.status_code}: {r.text}")
    return r.json()

# ── HTML ─────────────────────────────────────────────────────────────────

def build_html(crypto20, stocks_data, etf_data, vava, top_etf_data, indices_data, chart_urls, date_str, news=None):
    p1, p2, p3, p4, p5 = chart_urls

    def img(url, alt):
        return f'<img src="{url}" style="width:100%;border-radius:8px;margin:14px 0;border:1px solid #e5e7eb" alt="{alt}"/>'

    crypto_rows = ''
    for c in crypto20:
        col = hchg(c['change'])
        crypto_rows += f'''<tr>
          <td style="color:#9ca3af;text-align:center;font-size:11px">{c["rank"]}</td>
          <td><strong style="color:#1a56db">{c["ticker"]}</strong> <span style="color:#9ca3af;font-size:11px">{c["name"]}</span></td>
          <td style="color:#111827;font-weight:600">{fmt_price(c["price"])}</td>
          <td style="color:{col};font-weight:bold">{fmt_pct(c["change"])}</td>
          <td style="color:#6b7280;font-size:11px">{fmt_mcap(c["mcap"])}</td>
          <td style="color:#9ca3af;font-size:11px">{fmt_mcap(c["volume"])}</td>
        </tr>'''

    stock_rows = ''
    for sym in STOCK_SYMBOLS:
        d = stocks_data.get(sym, {}); col = hchg(d.get('change_pct'))
        stock_rows += f'''<tr>
          <td><strong style="color:#1a56db">{sym}</strong> <span style="color:#9ca3af;font-size:11px">{STOCK_NAMES.get(sym,"")}</span></td>
          <td style="color:#111827;font-weight:600">{fmt_price(d.get("price"))}</td>
          <td style="color:{col};font-weight:bold">{fmt_pct(d.get("change_pct"))}</td>
          <td style="color:#6b7280;font-size:11px">{fmt_mcap(d.get("market_cap"))}</td>
        </tr>'''

    etf_rows = ''
    for sym in ETF_SYMBOLS:
        d = etf_data.get(sym, {}); col = hchg(d.get('change_pct'))
        etf_rows += f'''<tr>
          <td><strong style="color:#1a56db">{sym}</strong><br><span style="color:#9ca3af;font-size:11px">{ETF_NAMES.get(sym,"")}</span></td>
          <td style="color:#111827;font-weight:600">{fmt_price(d.get("price"))} USD</td>
          <td style="color:{col};font-weight:bold">{fmt_pct(d.get("change_pct"))}</td>
          <td style="color:#6b7280;font-size:11px">{fmt_mcap(d.get("market_cap"))}</td>
        </tr>'''
    vc = hchg(vava.get('change_pct')); vp = vava.get('price')
    etf_rows += f'''<tr>
      <td><strong style="color:#1a56db">VAVA</strong><br><span style="color:#9ca3af;font-size:11px">VanEck Avalanche ETN (Xetra)</span></td>
      <td style="color:#111827;font-weight:600">{f"€{vp:.4f}" if vp else "—"} EUR</td>
      <td style="color:{vc};font-weight:bold">{fmt_pct(vava.get("change_pct"))}</td>
      <td style="color:#9ca3af;font-size:11px">{vava.get("note","")}</td>
    </tr>'''

    top_etf_rows = ''
    for sym in TOP_ETF_SYMBOLS:
        d = top_etf_data.get(sym, {}); col = hchg(d.get('change_pct'))
        top_etf_rows += f'''<tr>
          <td><strong style="color:#1a56db">{sym}</strong> <span style="color:#9ca3af;font-size:11px">{TOP_ETF_NAMES.get(sym,"")}</span></td>
          <td style="color:#111827;font-weight:600">{fmt_price(d.get("price"))}</td>
          <td style="color:{col};font-weight:bold">{fmt_pct(d.get("change_pct"))}</td>
          <td style="color:#6b7280;font-size:11px">{fmt_mcap(d.get("market_cap"))}</td>
        </tr>'''

    index_rows = ''
    for sym in INDEX_SYMBOLS:
        d = indices_data.get(sym, {}); col = hchg(d.get('change_pct'))
        index_rows += f'''<tr>
          <td><strong style="color:#1a56db">{INDEX_NAMES.get(sym,sym)}</strong></td>
          <td style="color:#111827;font-weight:600">{fmt_price(d.get("price")) if d.get("price") else "—"}</td>
          <td style="color:{col};font-weight:bold">{fmt_pct(d.get("change_pct"))}</td>
        </tr>'''

    # ── News HTML
    news_html = ''
    if news:
        news_items = ''
        for i, item in enumerate(news, 1):
            news_items += f'''<tr>
              <td style="color:#9ca3af;text-align:center;font-size:11px;width:24px">{i}</td>
              <td style="font-size:13px;padding:10px 12px">
                <a href="{item.get('link','#')}" target="_blank" style="color:#111827;text-decoration:none;font-weight:500;line-height:1.4">{item['title_uk']}</a>
                <br><span style="color:#9ca3af;font-size:11px">{item.get('source','')} · {item.get('pub','')[:16]}</span>
              </td>
            </tr>'''
        news_html = f'''<h2>📰 Новини за 24 години</h2>
<table>
  <tr><th>#</th><th>Заголовок</th></tr>
  {news_items}
</table>'''

    return f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,Arial,sans-serif;background:#f3f4f6;color:#111827;padding:24px;line-height:1.6}}
  .wrap{{max-width:700px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;box-shadow:0 2px 12px rgba(0,0,0,.08)}}
  h1{{color:#111827;font-size:22px;font-weight:800;margin-bottom:4px}}
  h2{{color:#1a56db;margin:28px 0 10px;font-size:15px;font-weight:700;border-left:3px solid #1a56db;padding-left:10px;text-transform:uppercase;letter-spacing:.4px}}
  .date{{display:inline-block;background:#eff6ff;color:#1a56db;padding:3px 12px;border-radius:20px;font-size:12px;font-weight:600;margin:6px 0 20px}}
  table{{width:100%;border-collapse:collapse;margin-bottom:6px;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb}}
  th{{background:#f9fafb;color:#6b7280;padding:9px 12px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #e5e7eb;font-weight:600}}
  td{{padding:9px 12px;border-bottom:1px solid #f3f4f6;font-size:13px}}
  tr:last-child td{{border-bottom:none}}
  .footer{{color:#9ca3af;font-size:11px;margin-top:24px;border-top:1px solid #e5e7eb;padding-top:12px}}
</style>
</head>
<body>
<div class="wrap">
<h1>📊 <span style="color:#1a56db">Ранковий</span> ринковий дайджест</h1>
<div class="date">🗓 {date_str}</div>
<a href="https://040d5yvqudeeuhzw0s13dcp9yjftfazu.runable.site" target="_blank"
   style="display:inline-flex;align-items:center;gap:8px;background:#1a56db;color:white;text-decoration:none;padding:10px 20px;border-radius:8px;font-weight:600;font-size:13px;margin:12px 0 20px">
  📈 Відкрити живі графіки TradingView →
</a>

<h2>📉 Індекси</h2>
<table><tr><th>Індекс</th><th>Рівень</th><th>Зміна</th></tr>{index_rows}</table>
{img(p5,"Індекси")}

<h2>🪙 Криптовалюти — Топ-20</h2>
<table><tr><th>#</th><th>Актив</th><th>Ціна (USD)</th><th>24г</th><th>Кап.</th><th>Обсяг 24г</th></tr>{crypto_rows}</table>
{img(p1,"Крипто Топ-20")}

<h2>📈 Акції США — Топ-10</h2>
<table><tr><th>Тікер</th><th>Ціна (USD)</th><th>Зміна</th><th>Кап.</th></tr>{stock_rows}</table>
{img(p2,"Акції Топ-10")}

<h2>🏦 Крипто ETF</h2>
<table><tr><th>ETF</th><th>Ціна</th><th>Зміна</th><th>Кап.</th></tr>{etf_rows}</table>
{img(p3,"Крипто ETF")}

<h2>📦 Топ-10 ETF</h2>
<table><tr><th>ETF</th><th>Ціна (USD)</th><th>Зміна</th><th>Кап.</th></tr>{top_etf_rows}</table>
{img(p4,"Топ ETF")}

{news_html}

<div class="footer">
  Джерела: CoinGecko API, yfinance (Yahoo Finance), CoinDesk, CoinTelegraph, Investing.com — дані зібрано о 09:00 CEST<br>
  Не є фінансовою порадою. Автоматичний щоденний звіт.
</div>
</div>
</body>
</html>'''

# ── MAIN ─────────────────────────────────────────────────────────────────

def main():
    now = datetime.datetime.now()
    date_str = now.strftime('%d %B %Y, %H:%M')
    subject_date = now.strftime('%d.%m.%Y')

    log("=== Morning Market Report (CI) ===")

    log("Fetching crypto top-20...")
    crypto20 = fetch_crypto_top20()
    log(f"  Got {len(crypto20)} coins")

    log("Fetching stocks...")
    stocks_data = fetch_yf(STOCK_SYMBOLS)

    log("Fetching ETFs...")
    etf_data = fetch_yf(ETF_SYMBOLS)
    top_etf_raw = fetch_yf(TOP_ETF_SYMBOLS)

    log("Fetching indices...")
    indices_data = fetch_yf(INDEX_SYMBOLS)

    log("Fetching VAVA...")
    vava = fetch_vava()
    top_etf_raw['VAVA'] = {'price': vava.get('price'), 'change_pct': vava.get('change_pct'), 'market_cap': None}

    log("Generating charts...")
    chart_paths = generate_charts(crypto20, stocks_data, etf_data, top_etf_raw, indices_data)

    log("Uploading charts to Cloudinary...")
    chart_urls = [cloudinary_upload(p) for p in chart_paths]
    log(f"  Uploaded {len(chart_urls)} charts")

    log("Fetching news...")
    news = fetch_news()
    log(f"  Got {len(news)} news items")

    log("Building HTML...")
    html = build_html(crypto20, stocks_data, etf_data, vava, top_etf_raw, indices_data, chart_urls, date_str, news)

    log(f"Sending email to {RECIPIENT}...")
    result = send_email(f'📊 Ранковий дайджест ринків — {subject_date}', html)
    log(f"✅ Done! Email ID: {result.get('id')}")

if __name__ == '__main__':
    main()
