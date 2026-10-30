from __future__ import annotations

from core4_portfolio_allocator.paper_forward.research_cloud import (
    ResearchCloudSupervisor,
    connected_count,
    format_research_status,
)


class TelegramStub:
    def send(self, _text: str) -> dict[str, bool]:
        return {"ok": True}


def test_disabled_supervisor_is_safe() -> None:
    supervisor = ResearchCloudSupervisor(TelegramStub(), enabled=False)
    assert supervisor.snapshot()["status"] == "DISABLED"
    assert supervisor.healthy() is True


def test_connected_count_requires_named_streams() -> None:
    status = {"streams": {"binance-futures": "CONNECTED", "bybit": "RECONNECTING"}}
    assert connected_count(status, ("binance-futures", "bybit", "hyperliquid")) == (1, 3)


def test_research_message_distinguishes_labels_from_trade_signals() -> None:
    payload = {
        "status": "OK",
        "flow_collector": {
            "streams": {
                "binance-futures": "CONNECTED",
                "bybit": "CONNECTED",
                "hyperliquid": "CONNECTED",
            }
        },
        "positioning_collector": {
            "streams": {
                "binance-context": "CONNECTED",
                "binance-liquidation": "CONNECTED",
                "bybit": "CONNECTED",
                "hyperliquid": "CONNECTED",
            }
        },
        "pipeline": {
            "flow_audit": {"common_contiguous_tail_minutes": 500},
            "positioning_audit": {"common_contiguous_tail_minutes": 500},
            "dataset": {
                "sample_rows": 12,
                "samples_by_timeframe": {"1h": 8, "4h": 4},
                "labels": {"LONG": 5, "SHORT": 4, "NO_TRADE": 3},
            },
            "screen": {
                "independent_candidates": 2,
                "independent_days": 1,
                "development_minimum_clusters": 30,
                "development_minimum_days": 5,
                "observational_metrics_not_validated": {
                    "win_rate": 0.5,
                    "profit_factor": 1.2,
                    "mean_r": 0.1,
                },
            },
        },
    }
    message = format_research_status(payload)
    assert "1h <code>8</code>" in message
    assert "4h <code>4</code>" in message
    assert "KHÔNG PHẢI TÍN HIỆU VÀO LỆNH" in message

