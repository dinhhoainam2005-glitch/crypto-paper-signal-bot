# CORE4 V19 Regime Allocator Protocol

## Purpose

Test whether a causal regime allocator can retain the V7 return while adding the
cross-market-confirmed FAST 20/10 BNB/SOL sleeve only in favorable states.

## Frozen sleeves

- Core: V7 55/20, CORE4, BTC SMA200 direction filter.
- Fast: 20/10, BNB/SOL tradable, at least two simultaneous CORE4 signals.
- When a valid fast request exists, new entries receive 60% core and 40% fast
  risk budgets. Otherwise new V7 entries retain 100% of their normal budget.
- Existing positions are never resized when the regime changes.

## Causal context

All context is measured at the prior completed daily close:

- BTC close versus SMA200;
- 20-day BTC SMA200 slope;
- BTC 20-day realized volatility versus its rolling 252-day median;
- number of CORE4 markets aligned with the BTC SMA200 regime.

## Finite router set

1. disabled (V7 only);
2. always allow the confirmed fast sleeve;
3. trend plus breadth;
4. high-volatility trend plus breadth;
5. low-volatility trend plus breadth;
6. high volatility plus breadth;
7. bearish high-volatility stress only.

No threshold search is permitted after seeing results.

## Walk-forward selection

- 2021-2022 are the initial training history and run V7 only in the stitched path.
- For every year from 2023 onward, a router may be selected using only prior
  years.
- A router must beat the prior-history V7 on return, PF, Sharpe and drawdown,
  and improve frequency by at least 10%; otherwise that year remains V7 only.

## Replacement gates

The stitched V19 path must pass all fourteen V18 replacement gates, including
25% higher frequency with no degradation in win rate, PF, mean R, return,
Sharpe, drawdown or completed-year consistency.

V19 is research only. A passing historical result may start a new paper-shadow
ledger but cannot authorize production replacement or real-money trading.
