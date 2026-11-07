# Royalty Calculator

**Author:** Chris Wokral · **License:** MIT

Command-line tool that reads a synthetic sales and streams register (CSV) plus contract terms (JSON) and writes a per-artist royalty statement (CSV) and a plain-text summary, including reserve withholding and recoupment of a label advance.

## What it does

1. For each sales line, applies packaging and distribution deductions to `net_revenue`.
2. Multiplies the result by the channel royalty rate and territory multiplier.
3. Withholds a contractual reserve from gross royalty.
4. Applies a single recoupable advance against artist payables in alphabetical order until the advance is exhausted.

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

| artist | gross_revenue | gross_royalty | reserve_withheld | payable_royalty |
|--------|---------------|---------------|------------------|-----------------|
| Lena Orchid | 504.50 | 80.12 | 16.02 | 64.10 |
| Miles Hollow | 884.00 | 113.13 | 22.63 | 90.50 |
| The Silver Pines | 196.00 | 32.16 | 6.43 | 25.73 |

After recoupment of a $450.00 advance, total cash payable is **$0.00** (all royalties applied to recoupment) and the advance balance remaining is **$269.67**.

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
- Integration tests load `samples/sales_streams.csv` and compare per-artist gross revenue and royalty totals to independently summed expectations.
- Recoupment tests verify FIFO application by artist name.
- A CLI integration test runs the full pipeline and checks summary figures for total payable and remaining advance.
