#!/usr/bin/env python3
"""
Canadian & Québec Fiscal Sankey Diagrams

Generates interactive Sankey visualizations of federal and provincial
government finances from public data sources.

Usage:
    canadian-fiscal-sankey [--options]

For detailed options, run:
    canadian-fiscal-sankey --help
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict

import pandas as pd

from . import parsers, visualization
from .utils import download, pdf_text

# Data source URLs
STATCAN_FED_GFS_ZIP = "https://www150.statcan.gc.ca/n1/en/tbl/csv/10100016-eng.zip"
QC_VOL1_CSV = "https://www.donneesquebec.ca/recherche/dataset/55b05d9f-93d1-450f-a8b9-c761b0c001a8/resource/a6e9462c-d415-4344-b6fe-43271d373b53/download/donnees_ouvertes_vol1_statistiques_24-25.csv"
QC_VOL2_CSV = "https://www.donneesquebec.ca/recherche/dataset/f94cd34c-8202-4cfe-9610-6ae10bf34bc3/resource/11699738-f16e-4b10-90e2-302d8c54128e/download/donnees_ouvertes_vol2_dep_supercat_24-25.csv"
CAN_FM_INDEX = "https://www.canada.ca/en/department-finance/services/publications/fiscal-monitor.html"
QC_PUB_INDEX = "https://www.quebec.ca/en/government/departments-agencies/finances/publications"

DEFAULT_SOURCE_DIR = Path("source_data")


def die(msg: str) -> None:
    """Exit with error message."""
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    """Main entry point for CLI."""
    ap = argparse.ArgumentParser(
        description="Generate Sankey diagrams for Canadian federal and Québec fiscal data"
    )
    ap.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR))
    ap.add_argument("--refresh", action="store_true", help="Re-download all source data")
    ap.add_argument("--out", default="outputs", help="Output directory for charts")
    ap.add_argument("--write-csv", default="", help="Export data to CSV file")
    ap.add_argument("--dump-text", action="store_true", help="Save extracted PDF text for debugging")
    ap.add_argument("--strict", action="store_true", help="Fail on missing PDF items (default: relaxed)")
    
    # Selection options
    ap.add_argument(
        "--can-annual-ref-date", 
        default="", 
        help="StatsCan REF_DATE (e.g., 2023). Default: latest."
    )
    ap.add_argument("--can-fm-year", type=int, default=None, help="Fiscal Monitor year (YYYY)")
    ap.add_argument("--can-fm-month", type=int, default=None, help="Fiscal Monitor month (MM)")
    ap.add_argument(
        "--qc-annual-year", 
        default="", 
        help="Québec CSV year value. Default: latest."
    )
    ap.add_argument(
        "--qc-fin-year", 
        default="", 
        help="Québec PDF report year token (e.g., 2025). Default: latest."
    )
    ap.add_argument(
        "--list-can-fm", 
        action="store_true", 
        help="List available Fiscal Monitor months and exit"
    )
    ap.add_argument(
        "--include-ytd", 
        action="store_true", 
        help="Include YTD charts from PDFs (default: annual only)"
    )

    args = ap.parse_args()
    
    if args.list_can_fm:
        # TODO: Implement fiscal monitor discovery
        die("--list-can-fm not yet implemented in refactored version")
        return 0

    source_dir = Path(args.source_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Fetch sources
    print("Fetching data sources...")
    statcan_zip = source_dir / "statcan_10100016_eng.zip"
    if args.refresh or not statcan_zip.exists():
        download(STATCAN_FED_GFS_ZIP, statcan_zip)
    
    qc_vol1_csv = source_dir / "qc_public_accounts_vol1_latest.csv"
    if args.refresh or not qc_vol1_csv.exists():
        download(QC_VOL1_CSV, qc_vol1_csv)
    
    qc_vol2_csv = source_dir / "qc_public_accounts_vol2_detailed_latest.csv"
    if args.refresh or not qc_vol2_csv.exists():
        download(QC_VOL2_CSV, qc_vol2_csv)

    # Parse data
    print("Parsing federal data...")
    try:
        can_period, can_rec, can_out = parsers.parse_statcan_federal_gfs_zip(
            statcan_zip,
            ref_date_choice=args.can_annual_ref_date or None,
        )
    except Exception as e:
        die(f"Failed to parse StatsCan data: {e}")

    print("Parsing Québec data...")
    try:
        qc_period, qc_rec, qc_out = parsers.parse_qc_vol1_csv(
            qc_vol1_csv,
            year_choice=args.qc_annual_year or None,
        )
    except Exception as e:
        die(f"Failed to parse Québec data: {e}")

    # Collect data rows
    rows = []
    def add_block(gov: str, period: str, rec: Dict[str, float], out: Dict[str, float], source: str):
        for k, v in rec.items():
            rows.append({
                "government": gov,
                "period": period,
                "kind": "receipt",
                "category": k,
                "amount": float(v),
                "unit": "CAD_billion",
                "source": source,
            })
        for k, v in out.items():
            rows.append({
                "government": gov,
                "period": period,
                "kind": "outlay",
                "category": k,
                "amount": float(v),
                "unit": "CAD_billion",
                "source": source,
            })

    add_block("Canada (federal)", can_period, can_rec, can_out, "StatsCan 10-10-0016-01")
    add_block("Québec", qc_period, qc_rec, qc_out, "Données Québec Vol 1")

    df = pd.DataFrame(rows)
    
    # Export CSV if requested
    if args.write_csv:
        out_csv = Path(args.write_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_csv, index=False)
        print(f"Data exported to {out_csv}")

    # Generate charts
    print(f"Generating Sankey diagrams...")
    charts = [
        ("Canada (federal)", can_period, can_rec, can_out),
        ("Québec", qc_period, qc_rec, qc_out),
    ]
    
    for gov, per, rec, out in charts:
        # Extract fiscal year
        year_match = re.search(r'(\d{4}-\d{2,4}|\d{4})', per)
        fiscal_year = year_match.group(1) if year_match else "Unknown"
        
        title = f"{gov} — Fiscal Year {fiscal_year}: {per} (CAD $B)"
        
        fig = visualization.sankey_figure(
            title, rec, out, 
            fiscal_period=f"Fiscal Year {fiscal_year} ({per})"
        )
        
        # Safe filename slug
        slug = f"{gov}_{per}".replace(" ", "_").replace("/", "_").replace("—", "-").replace("–", "-").replace("(", "").replace(")", "")
        html_path = out_dir / f"sankey_{slug}.html"
        
        fig.write_html(str(html_path), include_plotlyjs="cdn")
        print(f"  → {html_path}")
        
        # Try PNG export (may fail if kaleido not installed)
        try:
            fig.write_image(str(html_path.with_suffix(".png")), scale=2)
            print(f"  → {html_path.with_suffix('.png')}")
        except Exception as e:
            print(f"  ⚠ PNG export failed (install kaleido for PNG support): {e}")

    # Generate summary
    summary = (
        df.groupby(["government", "period", "kind"], as_index=False)["amount"].sum()
          .pivot_table(index=["government", "period"], columns="kind", values="amount", aggfunc="sum")
          .reset_index()
    )
    summary["deficit_or_surplus"] = summary.get("receipt", 0) - summary.get("outlay", 0)
    summary.to_csv(out_dir / "summary_totals.csv", index=False)

    print("\nDone!")
    print(f"Sources saved in: {source_dir}")
    print(f"Outputs saved in: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
