# Royalty Calculator

**Author:** Chris Wokral · **License:** MIT

Command-line tool that reads a synthetic sales and streams register (CSV) plus contract terms (JSON) and writes a per-artist royalty statement (CSV) and a plain-text summary, including reserve withholding and recoupment of a label advance.

## What it does

1. For each sales line, converts `net_revenue` to the statement currency using the contract's FX rates (lines without a currency are already in statement currency).
2. Applies packaging and distribution deductions to `net_revenue`.
3. Multiplies the result by the channel royalty rate and territory multiplier.
4. Withholds a contractual reserve from gross royalty.
5. Applies a single recoupable advance against artist payables in alphabetical order until the advance is exhausted.

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

| artist | gross_revenue | gross_royalty | reserve_withheld | payable_royalty | original_revenue | fx_converted_revenue |
|--------|---------------|---------------|------------------|-----------------|------------------|----------------------|
| Lena Orchid | 612.50 | 94.17 | 18.83 | 0.00 | EUR 100.00; USD 504.50 | 108.00 |
| Miles Hollow | 979.76 | 129.25 | 25.85 | 0.00 | GBP 75.40; USD 884.00 | 95.76 |
| The Silver Pines | 378.50 | 54.26 | 10.85 | 0.00 | CAD 250.00; USD 196.00 | 182.50 |

Before recoupment the artists are owed $75.34, $103.40 and $43.41 (gross royalty minus reserve). All $222.15 is applied against the $450.00 advance, so total cash payable is **$0.00** and the advance balance remaining is **$227.85**.

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

The statement CSV keeps all existing columns and adds `statement_currency`, `original_revenue` (per-currency totals as entered, e.g. `EUR 100.00; USD 504.50`) and `fx_converted_revenue` (the converted amount, in statement currency, of the lines that needed FX). A currency with no rate stops the run with an error naming the currency and CSV line, e.g. `Unknown currency 'JPY' on line 3: add an fx_rates entry converting JPY to USD`.

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
- Recoupment tests verify FIFO application by artist name.
- A CLI integration test runs the full pipeline and checks summary figures for total payable and remaining advance.
