"""
Generate equity curve chart: Strategy vs SPY vs QQQ (2010-2025).
Reads year_results from sp500_backtest progress or re-runs if needed.
"""

import json, math, sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

PROGRESS = Path('data/sp500_progress.json')
OUT_PATH = Path('chart_equity_curve.png')
INITIAL  = 100_000.0


def load_year_results():
    if PROGRESS.exists():
        data = json.loads(PROGRESS.read_text())
        yr = data.get('year_results', [])
        if yr and yr[-1].get('year', 0) >= 2025:
            return yr
        print('Progress file incomplete — re-running backtest...')
    else:
        print('No progress file — running backtest...')

    from sp500_backtest import run_sp500_backtest
    run_sp500_backtest()
    data = json.loads(PROGRESS.read_text())
    return data['year_results']


def build_series(year_results):
    valid = [yr for yr in year_results
             if not (isinstance(yr.get('portfolio_value'), float)
                     and math.isnan(yr['portfolio_value']))]

    years      = [yr['year'] for yr in valid]
    strat_vals = [yr['portfolio_value'] for yr in valid]

    spy_val = INITIAL
    qqq_val = INITIAL
    spy_vals = []
    qqq_vals = []
    for yr in valid:
        spy_val *= (1 + yr.get('spy_return', 0))
        qqq_val *= (1 + yr.get('qqq_return', 0))
        spy_vals.append(spy_val)
        qqq_vals.append(qqq_val)

    return years, strat_vals, spy_vals, qqq_vals


def plot(years, strat, spy, qqq):
    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=100)

    bg      = '#1a1a2e'
    teal    = '#00d2a0'
    gray    = '#8e8e9e'
    ltblue  = '#5dade2'
    gridclr = '#2a2a4e'

    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    ax.plot(years, strat, color=teal, linewidth=2.8, label='Algo Strategy', zorder=3)
    ax.plot(years, spy, color=gray, linewidth=1.4, label='SPY (S&P 500)', zorder=2)
    ax.plot(years, qqq, color=ltblue, linewidth=1.4, label='QQQ (Nasdaq)', zorder=2)

    ax.fill_between(years, strat, alpha=0.08, color=teal)

    offset_y = max(strat[-1], spy[-1], qqq[-1]) * 0.03
    vals = sorted([(strat[-1], teal, 'Strategy'), (spy[-1], gray, 'SPY'),
                   (qqq[-1], ltblue, 'QQQ')], reverse=True)
    placed = []
    for val, clr, lbl in vals:
        y = val
        for py in placed:
            if abs(y - py) < offset_y:
                y = py - offset_y
        placed.append(y)
        ax.annotate(f'${val:,.0f}',
                    xy=(years[-1], val),
                    xytext=(15, 0), textcoords='offset points',
                    color=clr, fontsize=10, fontweight='bold',
                    va='center',
                    arrowprops=dict(arrowstyle='-', color=clr, lw=0.8))

    first_yr = years[0]
    last_yr  = years[-1]
    ax.set_title(f'Algorithmic Strategy vs S&P 500 vs Nasdaq ({first_yr}–{last_yr})',
                 color='white', fontsize=15, fontweight='bold', pad=16)
    ax.set_xlabel('Year', color='#cccccc', fontsize=11)
    ax.set_ylabel('Portfolio Value ($)', color='#cccccc', fontsize=11)

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'${x:,.0f}'))
    ax.set_xticks(years)
    ax.set_xticklabels([str(y) for y in years], rotation=45, ha='right')

    ax.tick_params(colors='#999999', labelsize=9)
    ax.grid(True, color=gridclr, linewidth=0.5, alpha=0.6)

    for spine in ax.spines.values():
        spine.set_color('#333355')

    legend = ax.legend(loc='upper left', fontsize=10, framealpha=0.15,
                       edgecolor='#444466', facecolor=bg)
    for text in legend.get_texts():
        text.set_color('#dddddd')

    n_years = last_yr - first_yr
    mult = strat[-1] / INITIAL
    cagr = mult ** (1 / n_years) - 1 if n_years > 0 else 0
    ax.text(0.98, 0.04,
            f'Strategy: {mult:.1f}x  |  CAGR: {cagr:.1%}',
            transform=ax.transAxes, ha='right', va='bottom',
            color=teal, fontsize=10, fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.4', facecolor=bg,
                      edgecolor='#333355', alpha=0.9))

    plt.subplots_adjust(right=0.88)
    fig.savefig(OUT_PATH, dpi=100, facecolor=bg, bbox_inches='tight')
    plt.close(fig)
    print(f'\nChart saved to: {OUT_PATH.resolve()}')


if __name__ == '__main__':
    yr_data = load_year_results()
    years, strat, spy, qqq = build_series(yr_data)
    plot(years, strat, spy, qqq)
