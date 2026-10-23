# CORE4 V24 Early Execution Overlay Protocol

## Purpose

V24 does not create a new 4h strategy. It tests whether the frozen V20 daily
signals can be entered earlier after a causal 4h close crosses the already
known daily channel. V20 selection, exits and risk logic remain unchanged.

## Stage-one viability gate

Before simulating tranches or changing any runtime code, enumerate every early
4h trigger and compare it with the final daily V20 decision:

- Precision: fraction of early triggers that remain valid at the daily close.
- Coverage: fraction of daily V20 entries detected early.
- Lead time: hours gained versus the next daily open.
- Confirmed price advantage: signed entry-price improvement in basis points.
- False-break return: next-daily-open return after 20 bps entry and 20 bps exit
  costs.
- Opportunity benefit: confirmed price improvement plus false-break cost,
  measured across every early trigger.

Four configurations are preregistered: CORE entries with one or two same-side
4h confirmations, and the complete V20 CORE+FAST router with the same two CORE
confirmation choices. FAST keeps its frozen two-market and TREND_BREADTH2
requirements.

Development is 2021-2023, validation is 2024, and 2025 onward is frozen. A
candidate must have positive opportunity benefit in both development and
validation before frozen results or a tranche simulator may be used.

Passing this gate authorizes only a second-stage paper research simulation. It
does not change V20, deploy code, or authorize real money.
