#!/usr/bin/env python3
"""Compute per-artist royalty statements from sales CSV and contract terms JSON."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class AdvanceDeal:
    deal_id: str
    artists: list[str]
    advance: Decimal


@dataclass
class DealRecoupResult:
    deal_id: str
    artists: list[str]
    opening: Decimal
    recouped: Decimal
    closing: Decimal


@dataclass
class ContractTerms:
    royalty_rates: dict[str, Decimal]
    territory_multipliers: dict[str, Decimal]
    packaging_deduction_pct: Decimal
    distribution_deduction_pct: Decimal
    recoupable_advance: Decimal
    reserve_pct: Decimal
    statement_currency: str = "USD"
    fx_rates: dict[str, Decimal] = field(default_factory=dict)
    advances: list[AdvanceDeal] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractTerms:
        advances: list[AdvanceDeal] = []
        for deal in data.get("advances", []):
            advances.append(
                AdvanceDeal(
                    deal_id=str(deal["deal_id"]).strip(),
                    artists=[str(a).strip() for a in deal["artists"]],
                    advance=Decimal(str(deal["advance"])),
                )
            )
        return cls(
            royalty_rates={k: Decimal(str(v)) for k, v in data["royalty_rates"].items()},
            territory_multipliers={
                k: Decimal(str(v)) for k, v in data["territory_multipliers"].items()
            },
            packaging_deduction_pct=Decimal(str(data["packaging_deduction_pct"])),
            distribution_deduction_pct=Decimal(str(data["distribution_deduction_pct"])),
            recoupable_advance=Decimal(str(data["recoupable_advance"])),
            reserve_pct=Decimal(str(data["reserve_pct"])),
            statement_currency=str(data.get("statement_currency", "USD")).strip().upper(),
            fx_rates={
                k.strip().upper(): Decimal(str(v))
                for k, v in data.get("fx_rates", {}).items()
            },
            advances=advances,
        )

    def rate_to_statement(self, currency: str) -> Decimal | None:
        """Rate that converts one unit of `currency` into the statement currency."""
        if currency == self.statement_currency:
            return Decimal("1")
        return self.fx_rates.get(currency)


@dataclass
class ArtistTotals:
    gross_revenue: Decimal = Decimal("0")
    packaging_deduction: Decimal = Decimal("0")
    distribution_deduction: Decimal = Decimal("0")
    net_for_royalty: Decimal = Decimal("0")
    gross_royalty: Decimal = Decimal("0")
    reserve_withheld: Decimal = Decimal("0")
    payable_royalty: Decimal = Decimal("0")
    original_revenue: dict[str, Decimal] = field(default_factory=dict)
    fx_converted_revenue: Decimal = Decimal("0")
    deal_id: str = ""
    advance_recouped: Decimal = Decimal("0")
    deal_advance_opening: Decimal | None = None
    deal_advance_closing: Decimal | None = None


def validate_advances(
    by_artist: dict[str, ArtistTotals], deals: list[AdvanceDeal]
) -> None:
    """Ensure each artist appears in at most one deal and every deal artist has sales."""
    seen: dict[str, str] = {}
    for deal in deals:
        for artist in deal.artists:
            if artist in seen:
                raise ValueError(
                    f"Artist {artist!r} appears in more than one advance deal "
                    f"({seen[artist]!r} and {deal.deal_id!r})"
                )
            seen[artist] = deal.deal_id
            if artist not in by_artist:
                raise ValueError(
                    f"Unknown artist {artist!r} in advance deal {deal.deal_id!r}: "
                    "no sales lines for this artist"
                )


def attribute_recoupment_pro_rata(
    payables: dict[str, Decimal], total_recoup: Decimal
) -> dict[str, Decimal]:
    """Split a deal-level recoupment across artists pro rata to pre-recoup payables.

  Each share is rounded half-up to cents; any penny remainder is assigned one cent
  at a time to artists in descending order of rounding remainder, then by name.
    """
    if total_recoup <= 0 or not payables:
        return {artist: Decimal("0") for artist in payables}
    pool = sum(payables.values())
    if pool <= 0:
        return {artist: Decimal("0") for artist in payables}

    artists = sorted(payables.keys())
    raw = {artist: payables[artist] / pool * total_recoup for artist in artists}
    attributed = {artist: money(raw[artist]) for artist in artists}
    diff = money(total_recoup - sum(attributed.values()))
    pennies = int(diff / Decimal("0.01"))
    if pennies == 0:
        return attributed

    step = Decimal("0.01")
    if pennies < 0:
        step = Decimal("-0.01")
        pennies = -pennies
    order = sorted(artists, key=lambda a: (raw[a] - attributed[a], a), reverse=True)
    for i in range(pennies):
        artist = order[i % len(order)]
        attributed[artist] = money(attributed[artist] + step)
    return attributed


def apply_deal_advances(
    by_artist: dict[str, ArtistTotals], deals: list[AdvanceDeal]
) -> tuple[Decimal, list[DealRecoupResult]]:
    """Recoup per-deal advances from pooled payable royalties (cross-collateralised)."""
    validate_advances(by_artist, deals)
    deal_results: list[DealRecoupResult] = []
    total_paid = Decimal("0")

    for deal in deals:
        payables = {
            artist: by_artist[artist].payable_royalty for artist in deal.artists
        }
        pool = money(sum(payables.values()))
        recouped = money(min(pool, deal.advance))
        closing = money(deal.advance - recouped)
        attributed = attribute_recoupment_pro_rata(payables, recouped)

        for artist in deal.artists:
            rec = attributed[artist]
            cash = money(payables[artist] - rec)
            t = by_artist[artist]
            t.deal_id = deal.deal_id
            t.advance_recouped = rec
            t.deal_advance_opening = deal.advance
            t.deal_advance_closing = closing
            t.payable_royalty = cash
            total_paid += cash

        deal_results.append(
            DealRecoupResult(
                deal_id=deal.deal_id,
                artists=list(deal.artists),
                opening=deal.advance,
                recouped=recouped,
                closing=closing,
            )
        )

    for artist, totals in by_artist.items():
        if totals.deal_id:
            continue
        totals.advance_recouped = Decimal("0")
        total_paid += totals.payable_royalty

    return money(total_paid), deal_results


def load_sales(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def line_currency(row: dict[str, str], terms: ContractTerms) -> str:
    """Currency of a sales line; a missing or blank `currency` means statement currency."""
    return (row.get("currency") or "").strip().upper() or terms.statement_currency


def convert_revenue(
    row: dict[str, str], terms: ContractTerms, line_no: int | None = None
) -> Decimal:
    """Convert a line's net_revenue to statement currency, rounded half-up to cents."""
    currency = line_currency(row, terms)
    rate = terms.rate_to_statement(currency)
    if rate is None:
        where = f"line {line_no}" if line_no is not None else f"row {row}"
        raise ValueError(
            f"Unknown currency {currency!r} on {where}: add an fx_rates entry "
            f"converting {currency} to {terms.statement_currency}"
        )
    return money(Decimal(row["net_revenue"]) * rate)


def line_royalty(
    row: dict[str, str], terms: ContractTerms, line_no: int | None = None
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Return gross_revenue, packaging, distribution, net_for_royalty, gross_royalty.

    All returned amounts are in statement currency: net_revenue is converted
    (and rounded to cents) first, then deductions and rates are applied.
    """
    revenue = convert_revenue(row, terms, line_no)
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
    # Line numbers match the CSV file: the header is line 1, first data row is line 2.
    for line_no, row in enumerate(sales, start=2):
        artist = row["artist"].strip()
        revenue, packaging, distribution, net_for_royalty, gross_royalty = line_royalty(
            row, terms, line_no
        )
        t = by_artist[artist]
        currency = line_currency(row, terms)
        t.original_revenue[currency] = t.original_revenue.get(
            currency, Decimal("0")
        ) + Decimal(row["net_revenue"])
        if currency != terms.statement_currency:
            t.fx_converted_revenue += revenue
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
        t.fx_converted_revenue = money(t.fx_converted_revenue)
        t.original_revenue = {c: money(a) for c, a in t.original_revenue.items()}
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
            by_artist[artist].advance_recouped = Decimal("0")
            total_paid += payable
            continue
        recoup = min(payable, remaining)
        paid = payable - recoup
        remaining -= recoup
        total_recouped += recoup
        total_paid += paid
        by_artist[artist].advance_recouped = money(recoup)
        by_artist[artist].payable_royalty = money(paid)
    return money(total_paid), money(total_recouped), money(remaining)


def format_original_revenue(original: dict[str, Decimal]) -> str:
    """Per-currency revenue as entered, e.g. 'EUR 100.00; USD 504.50'."""
    return "; ".join(f"{c} {original[c]:.2f}" for c in sorted(original))


def _deal_balance_cell(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:.2f}"


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
        "deal_id",
        "advance_recouped",
        "deal_advance_opening",
        "deal_advance_closing",
        "statement_currency",
        "original_revenue",
        "fx_converted_revenue",
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
                    "deal_id": t.deal_id,
                    "advance_recouped": f"{t.advance_recouped:.2f}",
                    "deal_advance_opening": _deal_balance_cell(t.deal_advance_opening),
                    "deal_advance_closing": _deal_balance_cell(t.deal_advance_closing),
                    "statement_currency": terms.statement_currency,
                    "original_revenue": format_original_revenue(t.original_revenue),
                    "fx_converted_revenue": f"{t.fx_converted_revenue:.2f}",
                }
            )


def format_summary(
    by_artist: dict[str, ArtistTotals],
    terms: ContractTerms,
    total_paid: Decimal,
    total_recouped: Decimal,
    advance_remaining: Decimal,
    deal_results: list[DealRecoupResult] | None = None,
) -> str:
    cur = "$" if terms.statement_currency == "USD" else f"{terms.statement_currency} "
    lines = [
        "Royalty statement summary (synthetic example)",
        "=" * 48,
        f"Reserve rate: {terms.reserve_pct * 100:.1f}%",
        f"Statement currency: {terms.statement_currency}",
    ]
    if deal_results:
        lines.append("Per-artist advances (cross-collateralised by deal)")
        for result in deal_results:
            lines.append(f"  Deal {result.deal_id} ({', '.join(result.artists)}):")
            lines.append(f"    Opening advance:       {cur}{result.opening:>10.2f}")
            lines.append(f"    Recouped this period:  {cur}{result.recouped:>10.2f}")
            lines.append(f"    Closing advance:       {cur}{result.closing:>10.2f}")
        lines.append("")
    else:
        lines.append(f"Recoupable advance: {cur}{terms.recoupable_advance:.2f}")
    if terms.fx_rates:
        rates = ", ".join(f"{c} {terms.fx_rates[c]}" for c in sorted(terms.fx_rates))
        lines.append(f"FX rates to {terms.statement_currency}: {rates}")
    lines.append("")
    for artist in sorted(by_artist.keys()):
        t = by_artist[artist]
        lines.append(f"Artist: {artist}")
        if t.deal_id:
            lines.append(f"  Advance deal:              {t.deal_id}")
            lines.append(f"  Advance recouped:        {cur}{t.advance_recouped:>10.2f}")
        lines.append(f"  Original revenue:        {format_original_revenue(t.original_revenue)}")
        lines.append(f"  Gross revenue:           {cur}{t.gross_revenue:>10.2f}")
        lines.append(f"  Packaging deduction:     {cur}{t.packaging_deduction:>10.2f}")
        lines.append(f"  Distribution deduction:  {cur}{t.distribution_deduction:>10.2f}")
        lines.append(f"  Net for royalty:         {cur}{t.net_for_royalty:>10.2f}")
        lines.append(f"  Gross royalty:           {cur}{t.gross_royalty:>10.2f}")
        lines.append(f"  Reserve withheld:        {cur}{t.reserve_withheld:>10.2f}")
        lines.append(f"  Payable (after recoup):  {cur}{t.payable_royalty:>10.2f}")
        lines.append("")
    if deal_results:
        total_recouped = money(sum(r.recouped for r in deal_results))
        lines.append(f"Total cash payable:        {cur}{total_paid:>10.2f}")
        lines.append(f"Total applied to recoupment: {cur}{total_recouped:>10.2f}")
    else:
        lines.append(f"Total paid to artists:     {cur}{total_paid:>10.2f}")
        lines.append(f"Total applied to recoupment: {cur}{total_recouped:>10.2f}")
        lines.append(f"Advance balance remaining:   {cur}{advance_remaining:>10.2f}")
    return "\n".join(lines) + "\n"


def run(sales_path: Path, contract_path: Path, statement_path: Path, summary_path: Path) -> str:
    with contract_path.open(encoding="utf-8") as f:
        terms = ContractTerms.from_dict(json.load(f))
    sales = load_sales(sales_path)
    by_artist = aggregate_by_artist(sales, terms)
    deal_results: list[DealRecoupResult] | None = None
    total_recouped = Decimal("0")
    advance_remaining = Decimal("0")
    if terms.advances:
        total_paid, deal_results = apply_deal_advances(by_artist, terms.advances)
        total_recouped = money(sum(r.recouped for r in deal_results))
    else:
        total_paid, total_recouped, advance_remaining = apply_recoupment(
            by_artist, terms.recoupable_advance
        )
    write_statement_csv(statement_path, by_artist, terms)
    summary = format_summary(
        by_artist,
        terms,
        total_paid,
        total_recouped,
        advance_remaining,
        deal_results=deal_results,
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