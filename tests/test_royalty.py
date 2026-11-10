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
    write_statement_csv,
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

        # Each artist = USD lines (unchanged totals) + one converted FX line.
        lena = by_artist["Lena Orchid"]
        # USD 504.50 + (100.00 EUR * 1.08 = 108.00) = 612.50
        self.assertEqual(lena.gross_revenue, Decimal("612.50"))
        # USD lines 80.12 + EUR line 14.05 = 94.17
        self.assertEqual(lena.gross_royalty, Decimal("94.17"))
        self.assertEqual(lena.reserve_withheld, Decimal("18.83"))  # 18.834
        self.assertEqual(lena.payable_royalty, Decimal("75.34"))  # 94.17 - 18.83
        self.assertEqual(
            lena.original_revenue, {"USD": Decimal("504.50"), "EUR": Decimal("100.00")}
        )
        self.assertEqual(lena.fx_converted_revenue, Decimal("108.00"))

        miles = by_artist["Miles Hollow"]
        # USD 884.00 + (75.40 GBP * 1.27 = 95.758 -> 95.76) = 979.76
        self.assertEqual(miles.gross_revenue, Decimal("979.76"))
        self.assertEqual(miles.gross_royalty, Decimal("129.25"))  # 113.13 + 16.12
        self.assertEqual(miles.payable_royalty, Decimal("103.40"))  # 129.25 - 25.85

        pines = by_artist["The Silver Pines"]
        # USD 196.00 + (250.00 CAD * 0.73 = 182.50) = 378.50
        self.assertEqual(pines.gross_revenue, Decimal("378.50"))
        self.assertEqual(pines.gross_royalty, Decimal("54.26"))  # 32.16 + 22.10
        self.assertEqual(pines.payable_royalty, Decimal("43.41"))  # 54.26 - 10.85

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
            # Advance 450; total payable before recoup = 75.34 + 103.40 + 43.41 = 222.15
            self.assertIn("222.15", text)
            self.assertIn("227.85", text)  # 450 - 222.15


def fx_terms(**overrides):
    data = {
        "royalty_rates": {"streaming": "0.18", "download": "0.22", "physical": "0.15"},
        "territory_multipliers": {"US": "1.00", "CA": "0.95", "EU": "0.85", "UK": "0.90"},
        "packaging_deduction_pct": "0.10",
        "distribution_deduction_pct": "0.05",
        "recoupable_advance": "0",
        "reserve_pct": "0.20",
        "statement_currency": "USD",
        "fx_rates": {"EUR": "1.08", "GBP": "1.27", "CAD": "0.73"},
    }
    data.update(overrides)
    return ContractTerms.from_dict(data)


def sale(artist, channel, territory, net_revenue, currency=None):
    row = {
        "artist": artist,
        "channel": channel,
        "territory": territory,
        "units": "1",
        "net_revenue": net_revenue,
    }
    if currency is not None:
        row["currency"] = currency
    return row


class MultiCurrency(unittest.TestCase):
    """Hand-checked conversions: net_revenue is converted and rounded to cents first."""

    def test_eur_streaming_line(self):
        rev, pkg, dist, net, gross = line_royalty(
            sale("Lena Orchid", "streaming", "EU", "100.00", "EUR"), fx_terms()
        )
        self.assertEqual(rev, Decimal("108.00"))  # 100.00 EUR * 1.08
        self.assertEqual(pkg, Decimal("10.80"))  # 108.00 * 0.10
        self.assertEqual(dist, Decimal("5.40"))  # 108.00 * 0.05
        self.assertEqual(net, Decimal("91.80"))  # 108.00 - 10.80 - 5.40
        self.assertEqual(gross, Decimal("14.05"))  # 91.80 * 0.18 * 0.85 = 14.0454

    def test_gbp_download_line_rounds_at_each_step(self):
        rev, pkg, dist, net, gross = line_royalty(
            sale("Miles Hollow", "download", "UK", "75.40", "GBP"), fx_terms()
        )
        self.assertEqual(rev, Decimal("95.76"))  # 75.40 * 1.27 = 95.758 -> 95.76
        self.assertEqual(pkg, Decimal("9.58"))  # 95.76 * 0.10 = 9.576 -> 9.58
        self.assertEqual(dist, Decimal("4.79"))  # 95.76 * 0.05 = 4.788 -> 4.79
        self.assertEqual(net, Decimal("81.39"))  # 95.76 - 9.58 - 4.79
        self.assertEqual(gross, Decimal("16.12"))  # 81.39 * 0.22 * 0.90 = 16.11522

    def test_cad_physical_line(self):
        rev, pkg, dist, net, gross = line_royalty(
            sale("The Silver Pines", "physical", "CA", "250.00", "cad"), fx_terms()
        )
        self.assertEqual(rev, Decimal("182.50"))  # 250.00 * 0.73 (code is case-insensitive)
        self.assertEqual(pkg, Decimal("18.25"))  # 182.50 * 0.10
        self.assertEqual(dist, Decimal("9.13"))  # 182.50 * 0.05 = 9.125 -> 9.13
        self.assertEqual(net, Decimal("155.12"))  # 182.50 - 18.25 - 9.13
        self.assertEqual(gross, Decimal("22.10"))  # 155.12 * 0.15 * 0.95 = 22.1046

    def test_blank_or_statement_currency_is_not_converted(self):
        terms = fx_terms()
        for currency in (None, "", "  ", "USD"):
            rev = line_royalty(sale("Lena Orchid", "streaming", "US", "312.50", currency), terms)[0]
            self.assertEqual(rev, Decimal("312.50"))

    def test_non_usd_statement_currency(self):
        # Statement in EUR: 50.00 USD * 0.92 = 46.00 EUR; EUR lines need no rate.
        terms = fx_terms(statement_currency="EUR", fx_rates={"USD": "0.92"})
        self.assertEqual(
            line_royalty(sale("Lena Orchid", "streaming", "US", "50.00", "USD"), terms)[0],
            Decimal("46.00"),
        )
        self.assertEqual(
            line_royalty(sale("Lena Orchid", "streaming", "US", "50.00"), terms)[0],
            Decimal("50.00"),
        )

    def test_unknown_currency_names_currency_and_line(self):
        sales = [
            sale("Lena Orchid", "streaming", "US", "10.00"),  # CSV line 2
            sale("Lena Orchid", "streaming", "EU", "10.00", "EUR"),  # line 3
            sale("Miles Hollow", "streaming", "US", "10.00", "JPY"),  # line 4
        ]
        with self.assertRaises(ValueError) as ctx:
            aggregate_by_artist(sales, fx_terms())
        message = str(ctx.exception)
        self.assertIn("'JPY'", message)
        self.assertIn("line 4", message)

    def test_statement_csv_has_original_and_converted_amounts(self):
        sales = [
            sale("Lena Orchid", "streaming", "US", "312.50", ""),
            sale("Lena Orchid", "streaming", "EU", "100.00", "EUR"),
            sale("Lena Orchid", "streaming", "EU", "50.00", "EUR"),
        ]
        terms = fx_terms()
        by_artist = aggregate_by_artist(sales, terms)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "statement.csv"
            write_statement_csv(out, by_artist, terms)
            import csv

            with out.open(newline="") as f:
                (row,) = list(csv.DictReader(f))
        # 312.50 + 108.00 + (50.00 * 1.08 = 54.00) = 474.50 USD
        self.assertEqual(row["gross_revenue"], "474.50")
        self.assertEqual(row["statement_currency"], "USD")
        self.assertEqual(row["original_revenue"], "EUR 150.00; USD 312.50")
        self.assertEqual(row["fx_converted_revenue"], "162.00")  # 108.00 + 54.00


class BackwardCompatibility(unittest.TestCase):
    def test_csv_without_currency_column_and_terms_without_fx(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sales_csv = tmp_path / "sales.csv"
            sales_csv.write_text(
                "artist,channel,territory,units,net_revenue\n"
                "Lena Orchid,streaming,US,12500,312.50\n",
                encoding="utf-8",
            )
            contract = tmp_path / "terms.json"
            contract.write_text(
                json.dumps(
                    {
                        "royalty_rates": {"streaming": "0.18"},
                        "territory_multipliers": {"US": "1.00"},
                        "packaging_deduction_pct": "0.10",
                        "distribution_deduction_pct": "0.05",
                        "recoupable_advance": "0",
                        "reserve_pct": "0.20",
                    }
                ),
                encoding="utf-8",
            )
            text = run(sales_csv, contract, tmp_path / "out.csv", tmp_path / "out.txt")
        # Same as the README hand-checked line: gross royalty 47.81,
        # reserve 47.81 * 0.20 = 9.562 -> 9.56, payable 47.81 - 9.56 = 38.25.
        self.assertIn("Statement currency: USD", text)
        self.assertIn("Original revenue:        USD 312.50", text)
        self.assertIn("47.81", text)
        self.assertIn("38.25", text)


if __name__ == "__main__":
    unittest.main()
