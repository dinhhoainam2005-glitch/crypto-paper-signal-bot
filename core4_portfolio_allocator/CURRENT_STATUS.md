# CORE4 Current Status

V7 is frozen as `PAPER_FORWARD_CANDIDATE` after passing all three evidence
layers:

- Primary OOS audit: 12/12.
- Independent pre-registered stress audit: 14/14.
- Exact 1h execution and event-time funding audit: 19/19.

The candidate is a low-frequency daily portfolio strategy, not a promise of
profit. It averages 0.528 trades/week across the portfolio in the full OOS
sample. BTC is the weakest individual sleeve (PF 1.155), while the portfolio,
leave-one-symbol-out tests, cost stress, and recent period all pass. No sleeve
was removed after viewing OOS results.

No production service, Telegram sender, Render setting, or live-money control
has been changed. Any paper-forward implementation must consume the frozen
`CANDIDATE_SPEC.json` and emit Entry, SL, TP1, TP2, TP3, validity, and maximum
holding time for every candidate signal.

The isolated paper-forward implementation is now built under `paper_forward/`.
It passed synthetic, persistence, risk-cap, live Binance read-only, and local
HTTP smoke tests. Its Telegram integration remains disabled and the separate
Render service has not been created or deployed.
