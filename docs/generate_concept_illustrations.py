"""
Generates the README concept illustrations (docs/concept_*.png) for titanbot.
Not part of the trading pipeline -- run manually after changing the diagrams:

    python docs/generate_concept_illustrations.py

Reuses titanbot's own SMC-chart color palette (see
src/titanbot/utils/trade_manager.py::_generate_smc_chart_png) so the README
illustrations look like the bot's real Telegram charts, not a generic template.
"""
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrow

# --- titanbot's own palette (from trade_manager.py::_generate_smc_chart_png) ---
BG = '#0d1117'
GRID = '#1e2a3a'
SPINE = '#2a3a4a'
TEXT = '#e0e0e0'
MUTED = '#888888'
BULL = '#26a69a'
BEAR = '#ef5350'
FVG_BULL_FILL, FVG_BULL_EDGE = '#0a3a2a', '#00cc88'
OB_BULL_FILL, OB_BULL_EDGE = '#1a4a3a', '#26a69a'
SSL_COLOR = '#ffaa44'
BSL_COLOR = '#4488ff'
ENTRY_COLOR = '#ffd700'
SL_COLOR = '#ff1744'
TP_COLOR = '#00c853'

DOCS_DIR = os.path.dirname(os.path.abspath(__file__))


def _style_ax(ax):
    ax.set_facecolor(BG)
    ax.tick_params(colors=MUTED, labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor(SPINE)
    ax.set_xticks([])
    ax.grid(axis='y', color=GRID, linewidth=0.4, zorder=0)


def _candle(ax, i, o, h, l, c, bar_w=0.6):
    color = BULL if c >= o else BEAR
    ax.plot([i, i], [l, h], color=color, linewidth=1.1, zorder=2)
    body_bot = min(o, c)
    body_h = max(abs(c - o), (h - l) * 0.03)
    ax.add_patch(mpatches.FancyBboxPatch(
        (i - bar_w / 2, body_bot), bar_w, body_h,
        boxstyle="square,pad=0", linewidth=0, facecolor=color, zorder=3,
    ))


def _price_tag(ax, x_end, price, label, color):
    ax.axhline(price, color=color, linewidth=1.3, linestyle='--', zorder=4)
    ax.text(x_end + 0.3, price, f'{label}: {price:.2f}',
            color='#0d1117', fontsize=9, va='center', ha='left',
            fontweight='bold', zorder=8,
            bbox=dict(facecolor=color, edgecolor='none', alpha=0.95, pad=3,
                      boxstyle='square,pad=0.3'))


# ============================================================
# 1) concept_smc_entry.png -- illustrates the REAL get_titan_signal() logic:
#    SSL-Sweep -> Ruecklauf in bullische FVG (Discount-Zone) -> Confirmation
#    -> Entry, SL unter Sweep-Low, TP am naechsten BSL.
# ============================================================
def make_smc_entry():
    # (open, high, low, close) -- hand-built to tell the story cleanly
    candles = [
        (100.0, 100.4, 99.6, 100.2),
        (100.2, 100.6, 99.9, 100.4),   # forms the swing low's shoulder
        (100.4, 100.5, 99.2, 99.4),    # dips
        (99.4, 99.6, 97.9, 98.0),      # swing low forms here (idx 3, low=97.9) -> SSL registered
        (98.0, 98.3, 97.6, 97.8),
        (97.8, 98.0, 97.3, 97.5),
        (97.5, 97.6, 96.6, 97.55),     # SWEEP candle: wick below 97.9 SSL, closes back above -> liquidity grab
        (97.55, 99.0, 97.5, 98.9),     # FVG candle 1 (impulse up)
        (98.9, 99.1, 98.7, 99.0),      # FVG middle candle (gap vs candle 9's low)
        (99.0, 100.2, 98.95, 100.1),   # FVG candle 3: low(100.1erratum) -- creates bullish FVG with candle 7's high
        (100.1, 100.3, 98.75, 98.85),  # pulls back INTO the FVG zone (discount)
        (98.85, 99.3, 98.8, 99.25),    # confirmation candle: bullish, closes back up -> ENTRY
        (99.25, 100.0, 99.2, 99.9),
        (99.9, 100.8, 99.85, 100.7),
        (100.7, 101.6, 100.6, 101.5),  # runs toward TP (next BSL)
    ]
    n = len(candles)
    fig, ax = plt.subplots(figsize=(13, 7))
    fig.patch.set_facecolor(BG)
    _style_ax(ax)

    for i, (o, h, l, c) in enumerate(candles):
        _candle(ax, i, o, h, l, c)

    ssl_price = 97.9
    entry_price = 99.25
    sl_price = 96.5   # below the sweep wick
    tp_price = 101.9  # next BSL target

    # SSL level (dotted, orange) up to the sweep point
    ax.plot([1.5, 6.3], [ssl_price, ssl_price], color=SSL_COLOR, linewidth=1.3, linestyle=':', zorder=4)
    ax.text(1.5, ssl_price + 0.12, 'SSL (Sell-Side-Liquidity)', color=SSL_COLOR, fontsize=9, fontweight='bold')

    # Sweep annotation
    ax.annotate('Liquidity Sweep\n(Wick unter SSL, Close darüber)',
                xy=(6, 96.6), xytext=(3.6, 95.6),
                color=SSL_COLOR, fontsize=9, fontweight='bold', ha='center',
                arrowprops=dict(arrowstyle='->', color=SSL_COLOR, lw=1.4))

    # Bullish FVG zone (candle 7 high .. candle 9 low, spanning candles 8-10 visually)
    fvg_top, fvg_bottom = 98.95, 99.0
    ax.add_patch(mpatches.FancyBboxPatch(
        (7.5, fvg_bottom - 0.05), n - 7.5, fvg_top - fvg_bottom + 0.05,
        boxstyle="square,pad=0", linewidth=0.8,
        edgecolor=FVG_BULL_EDGE, facecolor=FVG_BULL_FILL, alpha=0.45, zorder=1,
    ))
    ax.text(8, 100.05, 'Bullische FVG\n(Discount-Zone, pd_pct ≤ 0.5)', color=FVG_BULL_EDGE,
            fontsize=9, fontweight='bold', ha='left')

    # Confirmation + entry marker
    ax.plot(11, entry_price, marker='*', markersize=20, color=ENTRY_COLOR, zorder=6,
            markeredgecolor='#0d1117', markeredgewidth=0.8)
    ax.annotate('Confirmation-Kerze\n(bullisch) → Entry',
                xy=(11, entry_price), xytext=(11.8, 97.9),
                color=ENTRY_COLOR, fontsize=9, fontweight='bold', ha='left',
                arrowprops=dict(arrowstyle='->', color=ENTRY_COLOR, lw=1.4))

    _price_tag(ax, n - 1, tp_price, 'TP', TP_COLOR)
    _price_tag(ax, n - 1, entry_price, 'Entry', ENTRY_COLOR)
    _price_tag(ax, n - 1, sl_price, 'SL', SL_COLOR)

    ax.set_xlim(-1, n + 3.4)
    ax.set_ylim(95.0, 102.4)
    ax.set_title('titanbot SMC-Entry: Sweep → FVG (Discount) → Confirmation → Entry',
                 color=TEXT, fontsize=13, pad=14, fontweight='bold')
    ax.yaxis.tick_right()

    plt.tight_layout()
    out = os.path.join(DOCS_DIR, 'concept_smc_entry.png')
    fig.savefig(out, dpi=150, facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close(fig)
    print(f'saved {out}')


# ============================================================
# 2) robust_optimizer_findings.png -- Optimizer-Testergebnis pro Paar
#    (70/30 Split, >=29 Test-Trades), aktueller Stand.
# ============================================================
def make_robust_findings():
    pairs = ['ADA/1h', 'XRP/1h', 'ARB/30m', 'AVAX/1h', 'SOL/30m']
    test_pnl = [-4.63, 2.11, 8.33, 36.28, 11.54]

    fig, ax = plt.subplots(figsize=(11, 6))
    fig.patch.set_facecolor(BG)
    _style_ax(ax)
    ax.set_xticks(range(len(pairs)))
    ax.set_xticklabels(pairs, color=TEXT, fontsize=11)

    bars = ax.bar(range(len(pairs)), test_pnl,
                   color=[TP_COLOR if v > 0 else BEAR for v in test_pnl], zorder=3, width=0.5)

    ax.axhline(0, color=MUTED, linewidth=0.9, zorder=2)
    for b in bars:
        v = b.get_height()
        ax.text(b.get_x() + b.get_width() / 2, v + (0.9 if v >= 0 else -1.6),
                f'{v:+.1f}%', ha='center', fontsize=10, color=TEXT, fontweight='bold')

    ax.set_ylabel('Test-PnL % (OOS, ≥29 Trades)', color=TEXT, fontsize=10)
    ax.set_title('Optimizer-Testergebnis pro Paar (70/30-Split, robuste Stichprobe)',
                 color=TEXT, fontsize=13, pad=14, fontweight='bold')
    ax.tick_params(axis='y', colors=MUTED)

    plt.tight_layout()
    out = os.path.join(DOCS_DIR, 'robust_optimizer_findings.png')
    fig.savefig(out, dpi=150, facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close(fig)
    print(f'saved {out}')


if __name__ == '__main__':
    make_smc_entry()
    make_robust_findings()
