# Royalty Calculator

**Author:** Chris Wokral · **License:** MIT

Command-line tool that reads a synthetic sales and streams register (CSV) plus contract terms (JSON) and writes a per-artist royalty statement (CSV) and a plain-text summary, including reserve withholding and recoupment of label advances.

## What it does

1. For each sales line, converts `net_revenue` to the statement currency using the contract's FX rates (lines without a currency are already in statement currency).
2. Applies packaging and distribution deductions to `net_revenue`.
3. Multiplies the result by the channel royalty rate and territory multiplier.
4. Withholds a contractual reserve from gross royalty.
5. Recoups advances either:
   - **Legacy:** a single `recoupable_advance` applied against all artist payables in alphabetical order until the advance is exhausted; or
   - **Per-artist deals:** optional `advances` list with cross-collateralised recoupment per deal (pooled payable royalties, pro rata attribution).

All bundled data is fictional (synthetic artists and amounts).

## Quick start

```bash
python3 royalty_calculator.py samples/sales_streams.csv samples/contract_terms.json \
  -o samples/royalty_statement.csv -s samples/royalty_summary.txt
```

```bash
python3 -m unittest discover -s tests -v
```

## Example output (statement excerpt)

| artist | gross_royalty | reserve_withheld | payable_royalty | deal_id | advance_recouped | deal_advance_opening | deal_advance_closing |
|--------|---------------|------------------|-----------------|---------|------------------|----------------------|----------------------|
| Lena Orchid | 94.17 | 18.83 | 0.00 | deal-alpha | 75.34 | 300.00 | 121.26 |
| Miles Hollow | 129.25 | 25.85 | 0.00 | deal-alpha | 103.40 | 300.00 | 121.26 |
| The Silver Pines | 54.26 | 10.85 | 13.41 | deal-solo-pines | 30.00 | 30.00 | 0.00 |

Before recoupment the artists are owed $75.34, $103.40 and $43.41 (gross royalty minus reserve). Deal **deal-alpha** cross-collateralises Lena Orchid and Miles Hollow against a $300.00 advance and recoups their combined $178.74 this period (advance closing balance $121.26). Deal **deal-solo-pines** recoups $30.00 of The Silver Pines' $43.41 payable, leaving **$13.41** cash payable. **Total cash payable: $13.41.**

## Per-artist advances and cross-collateralisation

Optional contract field `advances` is a list of deals. Each deal has `deal_id`, `artists` (one or more names), and `advance` (opening unrecouped balance for the deal). All listed artists share one advance: their **pre-recoup payables are pooled**, and the deal recoups `min(pool total, opening advance)` from that pool. Artists not named in any deal are paid in full. Each artist may appear in **at most one** deal; duplicates raise an error. An artist named in a deal but missing from the sales file raises an error.

Recouped amounts are **attributed back to artists pro rata** to their pre-recoup payables, with half-up rounding to cents. If attributed cents do not sum exactly to the deal recoupment, one-cent adjustments are applied in descending order of rounding remainder, then by artist name, until the total matches.

**Worked example (two-artist deal, partial period):**

| Artist | Pre-recoup payable | Share of pool | Attributed recoup | Cash payable |
|--------|-------------------|---------------|-------------------|--------------|
| Artist A | 50.00 | 50% | 17.50 | 32.50 |
| Artist B | 50.00 | 50% | 17.50 | 32.50 |
| **Deal total** | **100.00** | | **35.00** (advance was 35.00) | **65.00** |

Omit `advances` (or use an empty list) and set `recoupable_advance` to keep the original label-wide FIFO recoupment unchanged.

```json
"advances": [
  {
    "deal_id": "deal-alpha",
    "artists": ["Lena Orchid", "Miles Hollow"],
    "advance": "300.00"
  },
  {
    "deal_id": "deal-solo-pines",
    "artists": ["The Silver Pines"],
    "advance": "30.00"
  }
]
```

## Multi-currency

Sales lines can carry an optional `currency` column. A missing column or blank value means the line is already in the statement currency, so older CSVs work unchanged. Contract terms take an optional `statement_currency` (default `USD`) and `fx_rates`, mapping each currency code to the rate that converts one unit into the statement currency:

```json
"statement_currency": "USD",
"fx_rates": { "EUR": "1.08", "GBP": "1.27", "CAD": "0.73" }
```

Rounding: each line's `net_revenue` is converted and rounded half-up to cents **before** deductions; deductions, net and gross royalty are then rounded per line as usual, and totals are rounded again per artist.

Example: `Lena Orchid,streaming,EU,4000,100.00,EUR`

| Step | Calculation | Result |
|------|-------------|--------|
| Convert to USD | 100.00 × 1.08 | 108.00 |
| Packaging (10%) | 108.00 × 0.10 | 10.80 |
| Distribution (5%) | 108.00 × 0.05 | 5.40 |
| Net for royalty | 108.00 − 10.80 − 5.40 | 91.80 |
| Gross royalty | 91.80 × 0.18 × 0.85 | 14.05 |

The statement CSV keeps all prior columns and adds `deal_id`, `advance_recouped`, `deal_advance_opening`, `deal_advance_closing`, plus `statement_currency`, `original_revenue` (per-currency totals as entered, e.g. `EUR 100.00; USD 504.50`) and `fx_converted_revenue` (the converted amount, in statement currency, of the lines that needed FX). A currency with no rate stops the run with an error naming the currency and CSV line, e.g. `Unknown currency 'JPY' on line 3: add an fx_rates entry converting JPY to USD`.

## Hand-checked line (correctness anchor)

For **Lena Orchid**, one streaming row in **US** with `net_revenue` **312.50**:

| Step | Calculation | Result |
|------|-------------|--------|
| Packaging (10%) | 312.50 × 0.10 | 31.25 |
| Distribution (5%) | 312.50 × 0.05 | 15.63 |
| Net for royalty | 312.50 − 31.25 − 15.63 | 265.62 |
| Gross royalty | 265.62 × 0.18 × 1.00 | 47.81 |

The same rounding rules (`Decimal`, half-up to cents) are used for all lines and totals.

## How correctness is checked

- `tests/test_royalty.py` asserts the hand-checked row above.
- Multi-currency tests hand-check EUR, GBP and CAD lines, a non-USD statement currency, the unknown-currency error, and a CSV with no `currency` column.
- Integration tests load `samples/sales_streams.csv` and compare per-artist gross revenue and royalty totals to independently summed expectations.
- Per-artist advance tests cover pooled recoupment, partial recoupment, cash payable after solo recoupment, pro rata remainder attribution, validation errors, and legacy `recoupable_advance` FIFO (backward compatibility).
- A CLI integration test runs the full pipeline and checks summary figures for deal recoupment and total cash payable.