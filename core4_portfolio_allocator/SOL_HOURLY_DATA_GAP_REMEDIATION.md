# SOL Hourly Data-Gap Remediation

The frozen execution audit found two discontinuities in Binance's verified
SOLUSDT 1h monthly archives:

- 2022-02-26 00:00 through 2022-02-28 23:00 UTC: 72 hours.
- 2022-04-01 00:00 through 2022-04-02 23:00 UTC: 48 hours.

The official 1d and 5m trade-price archives omit the same dates, while official
funding events continue through both windows. No V7 SOL position overlaps
either window. The 99.9% coverage gate is nevertheless retained. Missing rows
are filled from checksum-verified official Binance USD-M 1h mark-price archives
for February and April 2022. Native trade-price 1h rows always take precedence,
and mark-price rows are used only where native rows do not exist. The provenance
of both the failed 5m repair source and the mark-price fallback is recorded in
`reports/sol_hourly_gap_repair_manifest.csv`.
