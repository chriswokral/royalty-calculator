#!/usr/bin/env python3
"""Unit tests with hand-checked arithmetic."""

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from royalty_calculator import (  # noqa: E402
    ArtistTotals,
    ContractTerms,
    aggregate_by_artist,
    apply_recoupment,
    line_royalty,
    run,
)


class HandCheckedExample(unittest.TestCase):
    """Single-row example documented in README."""

    def test_streaming_us_lena_orchid(self):
        terms = ContractTerms.from_dict(
            {
                "royalty_rates": {"streaming": "0.18"},
                "territory_multipliers": {"US": "1.00"},
                "packaging_deduction_pct": "0.10",
                "distribution_deduction_pct": "0.05",
                "recoupable_advance": "0",
                "reserve_pct": "0.20",
            }
        )
        row = {
            "artist": "Lena Orchid",
            "channel": "streaming",
            "territory": "US",
            "units": "12500",
            "net_revenue": "312.50",
        }
        rev, pkg, dist, net, gross = line_royalty(row, terms)
        self.assertEqual(rev, Decimal("312.50"))
        self.assertEqual(pkg, Decimal("31.25"))  # 10%
        self.assertEqual(dist, Decimal("15.63"))  # 5% of 312.50 -> 15.625
        self.assertEqual(net, Decimal("265.62"))  # 312.50 - 31.25 - 15.63
        self.assertEqual(gross, Decimal("47.81"))  # 265.63 * 0.18


class SampleFileIntegration(unittest.TestCase):
    def setUp(self):
        self.samples = ROOT / "samples"

    def test_aggregate_totals_match_expected(self):
        with (self.samples / "contract_terms.json").open() as f:
            terms = ContractTerms.from_dict(json.load(f))
        import csv

        with (self.samples / "sales_streams.csv").open(newline="") as f:
            sales = list(csv.DictReader(f))
        by_artist = aggregate_by_artist(sales, terms)

        lena = by_artist["Lena Orchid"]
        self.assertEqual(lena.gross_revenue, Decimal("504.50"))
        self.assertEqual(lena.gross_royalty, Decimal("80.12"))
        self.assertEqual(lena.reserve_withheld, Decimal("16.02"))
        self.assertEqual(lena.payable_royalty, Decimal("64.10"))

        miles = by_artist["Miles Hollow"]
        self.assertEqual(miles.gross_revenue, Decimal("884.00"))
        self.assertEqual(miles.gross_royalty, Decimal("113.13"))
        self.assertEqual(miles.payable_royalty, Decimal("90.50"))

        pines = by_artist["The Silver Pines"]
        self.assertEqual(pines.gross_revenue, Decimal("196.00"))
        self.assertEqual(pines.gross_royalty, Decimal("32.16"))
        self.assertEqual(pines.payable_royalty, Decimal("25.73"))

    def test_recoupment_fifo_by_artist_name(self):
        by_artist = {
            "Alpha": ArtistTotals(payable_royalty=Decimal("100.00")),
            "Beta": ArtistTotals(payable_royalty=Decimal("200.00")),
        }
        paid, recouped, remaining = apply_recoupment(by_artist, Decimal("150.00"))
        self.assertEqual(recouped, Decimal("150.00"))
        self.assertEqual(by_artist["Alpha"].payable_royalty, Decimal("0.00"))
        self.assertEqual(by_artist["Beta"].payable_royalty, Decimal("150.00"))
        self.assertEqual(paid, Decimal("150.00"))
        self.assertEqual(remaining, Decimal("0.00"))

    def test_cli_run_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stmt = tmp_path / "out.csv"
            summary = tmp_path / "out.txt"
            text = run(
                self.samples / "sales_streams.csv",
                self.samples / "contract_terms.json",
                stmt,
                summary,
            )
            self.assertIn("Advance balance remaining", text)
            self.assertTrue(stmt.exists())
            self.assertTrue(summary.exists())
            # Advance 450; total payable before recoup = 64.10 + 90.50 + 25.73 = 180.33
            self.assertIn("180.33", text)
            self.assertIn("269.67", text)  # 450 - 180.33


if __name__ == "__main__":
    unittest.main()
