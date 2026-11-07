#!/usr/bin/env python3
"""Compute per-artist royalty statements from sales CSV and contract terms JSON."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class ContractTerms:
    royalty_rates: dict[str, Decimal]
    territory_multipliers: dict[str, Decimal]
    packaging_deduction_pct: Decimal
    distribution_deduction_pct: Decimal
    recoupable_advance: Decimal
    reserve_pct: Decimal

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractTerms:
        return cls(
            royalty_rates={k: Decimal(str(v)) for k, v in data["royalty_rates"].items()},
            territory_multipliers={
                k: Decimal(str(v)) for k, v in data["territory_multipliers"].items()
            },
            packaging_deduction_pct=Decimal(str(data["packaging_deduction_pct"])),
            distribution_deduction_pct=Decimal(str(data["distribution_deduction_pct"])),
            recoupable_advance=Decimal(str(data["recoupable_advance"])),
            reserve_pct=Decimal(str(data["reserve_pct"])),
        )


@dataclass
class ArtistTotals:
    gross_revenue: Decimal = Decimal("0")
    packaging_deduction: Decimal = Decimal("0")
    distribution_deduction: Decimal = Decimal("0")
    net_for_royalty: Decimal = Decimal("0")
    gross_royalty: Decimal = Decimal("0")
    reserve_withheld: Decimal = Decimal("0")
    payable_royalty: Decimal = Decimal("0")


def load_sales(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def line_royalty(
    row: dict[str, str], terms: ContractTerms
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Return gross_revenue, packaging, distribution, net_for_royalty, gross_royalty."""
    revenue = Decimal(row["net_revenue"])
    channel = row["channel"].strip().lower()
    territory = row["territory"].strip().upper()

    rate = terms.royalty_rates.get(channel)
    if rate is None:
        raise ValueError(f"Unknown channel {channel!r} for row {row}")

    territory_mult = terms.territory_multipliers.get(territory)
    if territory_mult is None:
        raise ValueError(f"Unknown territory {territory!r} for row {row}")

    packaging = money(revenue * terms.packaging_deduction_pct)
    distribution = money(revenue * terms.distribution_deduction_pct)
    net_for_royalty = money(revenue - packaging - distribution)
    gross_royalty = money(net_for_royalty * rate * territory_mult)
    return revenue, packaging, distribution, net_for_royalty, gross_royalty


def aggregate_by_artist(
    sales: list[dict[str, str]], terms: ContractTerms
) -> dict[str, ArtistTotals]:
    by_artist: dict[str, ArtistTotals] = defaultdict(ArtistTotals)
    for row in sales:
        artist = row["artist"].strip()
        revenue, packaging, distribution, net_for_royalty, gross_royalty = line_royalty(
            row, terms
        )
        t = by_artist[artist]
        t.gross_revenue += revenue
        t.packaging_deduction += packaging
        t.distribution_deduction += distribution
        t.net_for_royalty += net_for_royalty
        t.gross_royalty += gross_royalty
    for t in by_artist.values():
        t.gross_revenue = money(t.gross_revenue)
        t.packaging_deduction = money(t.packaging_deduction)
        t.distribution_deduction = money(t.distribution_deduction)
        t.net_for_royalty = money(t.net_for_royalty)
        t.gross_royalty = money(t.gross_royalty)
        t.reserve_withheld = money(t.gross_royalty * terms.reserve_pct)
        t.payable_royalty = money(t.gross_royalty - t.reserve_withheld)
    return dict(by_artist)


def apply_recoupment(
    by_artist: dict[str, ArtistTotals], advance: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """Apply label-level recoupable advance against total payable royalties (FIFO by artist name)."""
    remaining = advance
    total_paid = Decimal("0")
    total_recouped = Decimal("0")
    for artist in sorted(by_artist.keys()):
        payable = by_artist[artist].payable_royalty
        if remaining <= 0:
            total_paid += payable
            continue
        recoup = min(payable, remaining)
        paid = payable - recoup
        remaining -= recoup
        total_recouped += recoup
        total_paid += paid
        by_artist[artist].payable_royalty = money(paid)
    return money(total_paid), money(total_recouped), money(remaining)


def write_statement_csv(
    path: Path, by_artist: dict[str, ArtistTotals], terms: ContractTerms
) -> None:
    fields = [
        "artist",
        "gross_revenue",
        "packaging_deduction",
        "distribution_deduction",
        "net_for_royalty",
        "gross_royalty",
        "reserve_withheld",
        "payable_royalty",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for artist in sorted(by_artist.keys()):
            t = by_artist[artist]
            w.writerow(
                {
                    "artist": artist,
                    "gross_revenue": f"{t.gross_revenue:.2f}",
                    "packaging_deduction": f"{t.packaging_deduction:.2f}",
                    "distribution_deduction": f"{t.distribution_deduction:.2f}",
                    "net_for_royalty": f"{t.net_for_royalty:.2f}",
                    "gross_royalty": f"{t.gross_royalty:.2f}",
                    "reserve_withheld": f"{t.reserve_withheld:.2f}",
                    "payable_royalty": f"{t.payable_royalty:.2f}",
                }
            )


def format_summary(
    by_artist: dict[str, ArtistTotals],
    terms: ContractTerms,
    total_paid: Decimal,
    total_recouped: Decimal,
    advance_remaining: Decimal,
) -> str:
    lines = [
        "Royalty statement summary (synthetic example)",
        "=" * 48,
        f"Reserve rate: {terms.reserve_pct * 100:.1f}%",
        f"Recoupable advance: ${terms.recoupable_advance:.2f}",
        "",
    ]
    for artist in sorted(by_artist.keys()):
        t = by_artist[artist]
        lines.append(f"Artist: {artist}")
        lines.append(f"  Gross revenue:           ${t.gross_revenue:>10.2f}")
        lines.append(f"  Packaging deduction:     ${t.packaging_deduction:>10.2f}")
        lines.append(f"  Distribution deduction:  ${t.distribution_deduction:>10.2f}")
        lines.append(f"  Net for royalty:         ${t.net_for_royalty:>10.2f}")
        lines.append(f"  Gross royalty:           ${t.gross_royalty:>10.2f}")
        lines.append(f"  Reserve withheld:        ${t.reserve_withheld:>10.2f}")
        lines.append(f"  Payable (after recoup):  ${t.payable_royalty:>10.2f}")
        lines.append("")
    lines.append(f"Total paid to artists:     ${total_paid:>10.2f}")
    lines.append(f"Total applied to recoupment: ${total_recouped:>10.2f}")
    lines.append(f"Advance balance remaining:   ${advance_remaining:>10.2f}")
    return "\n".join(lines) + "\n"


def run(sales_path: Path, contract_path: Path, statement_path: Path, summary_path: Path) -> str:
    with contract_path.open(encoding="utf-8") as f:
        terms = ContractTerms.from_dict(json.load(f))
    sales = load_sales(sales_path)
    by_artist = aggregate_by_artist(sales, terms)
    total_paid, total_recouped, advance_remaining = apply_recoupment(
        by_artist, terms.recoupable_advance
    )
    write_statement_csv(statement_path, by_artist, terms)
    summary = format_summary(
        by_artist, terms, total_paid, total_recouped, advance_remaining
    )
    summary_path.write_text(summary, encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build per-artist royalty statements from sales and contract terms."
    )
    parser.add_argument("sales_csv", type=Path, help="Sales/streams CSV path")
    parser.add_argument("contract_json", type=Path, help="Contract terms JSON path")
    parser.add_argument(
        "-o",
        "--statement",
        type=Path,
        default=Path("royalty_statement.csv"),
        help="Output statement CSV (default: royalty_statement.csv)",
    )
    parser.add_argument(
        "-s",
        "--summary",
        type=Path,
        default=Path("royalty_summary.txt"),
        help="Output summary text (default: royalty_summary.txt)",
    )
    args = parser.parse_args(argv)
    try:
        summary = run(args.sales_csv, args.contract_json, args.statement, args.summary)
        print(summary, end="")
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
