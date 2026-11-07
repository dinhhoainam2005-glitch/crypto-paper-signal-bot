# Supervised Forward Service

## Deployment status

Prepared and verified; deployment pending.

The Blueprint is `render.forward.yaml`. Automatic deploys are disabled so that
committing or pushing this file cannot create a paid service by itself.

## Render design

- Web service: `btc-market-structure-forward`
- Region: Frankfurt, avoiding US-region restrictions on Binance Futures data
- Compute plan: `0.5c-512mb` (legacy name: Starter)
- One instance only because an attached disk cannot be horizontally scaled
- Health check: `/health`
- Data readiness: `/ready`
- Full read-only state: `/status`
- Persistent disk: 2 GB mounted at `/var/data`
- Durable data root: `/var/data/forward`
- Five-minute capture sessions with 30-second overlap
- H001 detector and sealed evaluator every 320 committed segments,
  approximately daily; data collection and health heartbeat remain continuous
- Fixed study: 30 complete UTC days (15 calibration, 7 validation, 8 test)
- Collection pauses after the immutable sealed result is written
- Latest two completed raw segments retained; compact causal ledger retained
- Optional Vietnamese Telegram operational heartbeat every hour
- Telegram token and chat ID are secret environment variables, never committed

## Cost boundary

Checked against Render's official pricing on 2026-11-07:

- `0.5c-512mb`: USD 7 per month
- persistent disk: USD 0.25 per GB per month
- 2 GB persistent disk: USD 0.50 per month
- estimated base total for this new isolated service: USD 7.50 per month,
  before tax or excess bandwidth

Creating this service would be a new recurring charge unless another paid
service is suspended or replaced. No deployment should occur implicitly.

## Runtime boundary

This service collects research data only. It has no exchange keys, cannot place
orders, and does not produce a trade recommendation. A ready service means the
data pipeline is healthy; it does not mean H001 passed.

## Official references

- https://render.com/docs/blueprint-spec
- https://render.com/docs/compute-plans
- https://render.com/docs/disks
- https://render.com/docs/health-checks
- https://render.com/pricing
- https://core.telegram.org/bots/api#sendmessage
