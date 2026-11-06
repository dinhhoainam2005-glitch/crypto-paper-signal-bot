import unittest

from market_structure.book_replay import OrderBook, ReplayError


def event(venue, kind, **data):
    return {"venue": venue, "event_type": "book", "data": {"update_kind": kind, **data}}


class OrderBookReplayTests(unittest.TestCase):
    def test_binance_snapshot_stale_and_bridged_delta(self) -> None:
        book = OrderBook("binance")
        book.apply(event("binance", "snapshot", last_update_id=10, bids=[["100", "2"]], asks=[["101", "3"]]))
        book.apply(event("binance", "delta", first_update_id=8, last_update_id=10, previous_update_id=7, bids=[], asks=[]))
        book.apply(event("binance", "delta", first_update_id=11, last_update_id=12, previous_update_id=10, bids=[["100", "0"], ["99", "1"]], asks=[]))
        report = book.report()
        self.assertEqual(report["best_bid"], "99")
        self.assertEqual(report["stats"]["stale_deltas"], 1)
        self.assertEqual(report["stats"]["applied_deltas"], 1)

    def test_binance_gap_fails(self) -> None:
        book = OrderBook("binance")
        book.apply(event("binance", "snapshot", last_update_id=10, bids=[["100", "2"]], asks=[["101", "3"]]))
        with self.assertRaisesRegex(ReplayError, "does not bridge"):
            book.apply(event("binance", "delta", first_update_id=12, last_update_id=13, previous_update_id=11, bids=[], asks=[]))

    def test_bybit_snapshot_and_delta(self) -> None:
        book = OrderBook("bybit")
        book.apply(event("bybit", "snapshot", update_id=20, bids=[["100", "2"]], asks=[["101", "3"]]))
        book.apply(event("bybit", "delta", update_id=21, bids=[["100.5", "1"]], asks=[["101", "0"], ["102", "4"]]))
        report = book.report()
        self.assertEqual(report["best_bid"], "100.5")
        self.assertEqual(report["best_ask"], "102")

    def test_crossed_book_fails(self) -> None:
        book = OrderBook("bybit")
        with self.assertRaisesRegex(ReplayError, "crossed"):
            book.apply(event("bybit", "snapshot", update_id=1, bids=[["101", "1"]], asks=[["100", "1"]]))


if __name__ == "__main__":
    unittest.main()
