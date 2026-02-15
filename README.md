# can-qc-dynamic-sankey (v6)

Dynamic Sankey diagrams for Canadian federal and Québec provincial fiscal data with **enhanced visualizations** and **period selection** options.

## Features

- ��� **Enhanced Sankey Visualizations**: Colored flows (green for receipts, blue for outlays, red for deficit), value labels on all flows, proper deficit visualization
- ��� **Multiple Data Sources**: StatsCan, Finance Canada Fiscal Monitor, Québec Open Data, Québec Financial Situation
- ��� **Period Selection**: Choose specific fiscal years/months instead of always taking the latest
- ��� **CSV Export**: Export all data to structured CSV format

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Quick Start

Generate all charts with latest data:
```bash
python can_qc_dynamic_sankey.py --out outputs --write-csv outputs/raw.csv
```

This creates interactive HTML Sankey diagrams in the `outputs/` folder with:
- ✅ Color-coded flows (green receipts, blue outlays, red deficit)
- ✅ Dollar amounts displayed on each flow
- ✅ Deficit properly shown as inflow to balance the equation
- ✅ Summary totals and deficit/surplus highlighted in subtitle

## Visualization Details

The Sankey diagrams follow the US Treasury visualization style:
- **Receipts** (green) flow INTO the central node
- **Deficit** (red) flows INTO the central node to balance: `Receipts + Deficit = Outlays`
- **Outlays** (blue/teal) flow OUT of the central node
- All amounts are labeled (e.g., "$69.1B")
- Central node shows total receipts and total outlays

## Data Sources

Downloads and caches into `source_data/`:
- `statcan_10100016_eng.zip` - StatsCan Table 10-10-0016-01 (Canada federal GFS)
- `can_fiscal_monitor_<YYYY-MM>.pdf` - Latest Finance Canada Fiscal Monitor (YTD)
- `qc_public_accounts_vol1_latest.csv` - Québec Public Accounts Volume 1 (Open Data)
- `qc_financial_situation_<token>.pdf` - Latest Québec Financial Situation report

Generates Sankey diagrams for:
1. **Canada federal (annual)** - From StatsCan
2. **Canada federal (YTD)** - From Fiscal Monitor PDF
3. **Québec (annual)** - From Public Accounts CSV
4. **Québec (YTD)** - From Financial Situation PDF

---

## Period Selection Options

### Choose Canada annual year (StatsCan REF_DATE)

Select a specific year from StatsCan data:

```bash
python can_qc_dynamic_sankey.py --can-annual-ref-date 2023 --out outputs
```

### Choose Canada Fiscal Monitor month (YTD)

List available months on the Finance Canada website:

```bash
python can_qc_dynamic_sankey.py --list-can-fm
```

Output example:
```
2024-10
2024-11
2025-01
2025-10
...
```

Then select a specific month:

```bash
python can_qc_dynamic_sankey.py --can-fm-year 2025 --can-fm-month 10 --out outputs
```

### Choose Québec annual year (CSV)

Select a specific fiscal year from the Québec Public Accounts data:

```bash
# For numeric year
python can_qc_dynamic_sankey.py --qc-annual-year 2024 --out outputs

# For fiscal year string
python can_qc_dynamic_sankey.py --qc-annual-year "2024-2025" --out outputs
```

### Choose Québec Financial Situation report year (YTD)

Filter by year token in the PDF URL:

```bash
python can_qc_dynamic_sankey.py --qc-fin-year 2025 --out outputs
# or
python can_qc_dynamic_sankey.py --qc-fin-year "2025-26" --out outputs
```

---

## Additional Options

### Force refresh downloads

Re-download all source files (ignores cached data):

```bash
python can_qc_dynamic_sankey.py --refresh --out outputs
```

### Strict vs relaxed parsing (PDFs)

Default is **relaxed**: if one PDF line item can't be found, it skips it.

For **strict** mode (fail if any item is missing):

```bash
python can_qc_dynamic_sankey.py --strict --out outputs
```

### Debug PDF parsing

Extract PDF text to files for debugging:

```bash
python can_qc_dynamic_sankey.py --dump-text --out outputs
```

Then inspect:
- `outputs/can_fm_text.txt` - Canada Fiscal Monitor extracted text
- `outputs/qc_fin_text.txt` - Québec Financial Situation extracted text

---

## Output Files

After running, the `outputs/` folder contains:

**Sankey Diagrams** (interactive HTML):
- `sankey_Canada_federal_Canada_federal_annual_REF_DATE=2024_StatsCan_10-10-0016-01.html`
- `sankey_Canada_federal_Canada_federal_YTD_2025-10.html`
- `sankey_Québec_Québec_annual_2024-2025_Vol_1_CSV.html`
- `sankey_Québec_Québec_YTD_latest.html`

**Data Files**:
- `raw.csv` - All data in structured format (if `--write-csv` specified)
- `summary_totals.csv` - Summary with totals by government/period

---

## Example: Combining Multiple Options

Generate charts for specific Canada and Québec fiscal years with CSV export:

```bash
python can_qc_dynamic_sankey.py \
  --can-fm-year 2025 --can-fm-month 10 \
  --qc-annual-year "2024-2025" \
  --out outputs \
  --write-csv outputs/fiscal_data.csv
```

---

## Troubleshooting

**CSV parsing error**: The Québec CSV uses semicolons (`;`) as delimiters and may have spaces in numbers. The script handles this automatically.

**PDF parsing fails**: Use `--dump-text` to inspect extracted text and `--strict` to see which items are missing.

**No Fiscal Monitor found**: The script discovers the latest report from the Finance Canada website. Use `--list-can-fm` to see available periods.

---

## License

MIT
