# V7 BTC Beta-Regime Breakout Protocol

V7 keeps every V5 rule and adds exactly one cross-market direction constraint:

- A LONG breakout is eligible only when BTC closes above its SMA200.
- A SHORT breakout is eligible only when BTC closes below its SMA200.

The BTC filter uses the same completed decision day as the breakout. No current
bar or next-open value enters the decision. Channel lengths, ATR, TP/SL,
holding time, funding, costs, risk, and gross caps are unchanged from V5.

The protocol uses annual fold resets from 2021. No alternative moving-average
length or filter combination is tested. A pass only opens a mandatory second
audit; it cannot enable paper or live signals.
