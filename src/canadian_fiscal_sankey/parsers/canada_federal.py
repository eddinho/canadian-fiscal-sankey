"""
Parser for Canadian federal government financial data from StatsCan.

StatsCan Table 10-10-0016-01: Canadian federal government financial statements
"""

import io
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    """Find column name (case-insensitive) from candidates."""
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def parse_statcan_federal_gfs_zip(
    zip_path: Path, 
    ref_date_choice: Optional[str] = None
) -> Tuple[str, Dict[str, float], Dict[str, float]]:
    """
    Parse StatsCan federal GFS ZIP file.
    
    Args:
        zip_path: Path to StatsCan 10-10-0016-01 ZIP file
        ref_date_choice: Specific REF_DATE to use (e.g., "2023"). If None, uses latest.
    
    Returns:
        Tuple of (period_label, receipts_dict, outlays_dict)
        Values are in billions (CAD)
    """
    with zipfile.ZipFile(zip_path, "r") as z:
        csv_names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise FileNotFoundError(f"No CSV found inside ZIP: {zip_path}")
        csv_name = max(csv_names, key=lambda n: z.getinfo(n).file_size)
        raw = z.read(csv_name)

    df = pd.read_csv(io.BytesIO(raw))
    ref_col = _pick_col(df, ["REF_DATE", "ref_date"])
    val_col = _pick_col(df, ["VALUE", "value"])
    if ref_col is None or val_col is None:
        raise ValueError(f"StatsCan CSV missing REF_DATE or VALUE. Columns={list(df.columns)}")

    # Choose REF_DATE
    if ref_date_choice:
        choices = df[ref_col].astype(str).unique().tolist()
        if ref_date_choice not in choices:
            if ref_date_choice.isdigit() and str(int(ref_date_choice)) in choices:
                ref_date_choice = str(int(ref_date_choice))
            else:
                recent = ", ".join(sorted(choices)[-10:])
                raise ValueError(
                    f"Requested --can-annual-ref-date '{ref_date_choice}' not found. "
                    f"Recent REF_DATE values: {recent}"
                )
        chosen = ref_date_choice
    else:
        chosen = str(df[ref_col].max())

    d = df[df[ref_col].astype(str) == chosen].copy()
    d["value_bil"] = pd.to_numeric(d[val_col], errors="coerce") / 1000.0  # millions -> billions
    d = d.dropna(subset=["value_bil"])

    # Filter for "Transactions and other economic flows"
    display_col = _pick_col(df, ["Display value", "Estimates"])
    if display_col and display_col in d.columns:
        d = d[d[display_col].astype(str).str.contains("transaction", case=False, na=False)]

    # Find category column
    cat_col = _pick_col(df, ["Statement of operations and balance sheet"])
    if cat_col is None:
        cat_col = _pick_col(df, ["Display value", "Public sector components"])
    if cat_col is None:
        dim_cols = [c for c in df.columns if c not in (
            ref_col, val_col, "VECTOR", "COORDINATE", "STATUS", "SYMBOL", 
            "TERMINATED", "DECIMALS", "DGUID", "GEO", "UOM", "UOM_ID", 
            "SCALAR_FACTOR", "SCALAR_ID"
        )]
        cat_col = dim_cols[0] if dim_cols else None
    if cat_col is None:
        raise ValueError("Could not pick a category column for StatsCan data.")

    # Classify revenue vs expense using bracket codes
    cat_series = d[cat_col].astype(str)
    d['bracket'] = cat_series.str.extract(r'\[(\d+)\]', expand=False)
    
    rec = d[d['bracket'].str.startswith('1', na=False)].copy()
    out = d[d['bracket'].str.startswith('2', na=False)].copy()

    if rec.empty and out.empty:
        rec = d[cat_series.str.contains("revenue|receipts|taxes|contributions", case=False, na=False) & 
                ~cat_series.str.contains("expense|expenditure", case=False, na=False)]
        out = d[cat_series.str.contains("expense|expenditure", case=False, na=False)]
        
    if rec.empty and out.empty:
        out = d

    def top_map(x: pd.DataFrame, n: int = 20) -> Dict[str, float]:
        """Extract top N Level 2 categories."""
        x_clean = x.copy()
        x_clean['cat_clean'] = x_clean[cat_col].astype(str).str.replace(
            r'\s*\[[\d,\s]+\]', '', regex=True
        ).str.strip()
        x_clean['bracket'] = x_clean[cat_col].astype(str).str.extract(
            r'\[(\d+)\]', expand=False
        ).fillna('')
        
        x_level2 = x_clean[x_clean['bracket'].str.len() == 2].copy()
        result = x_level2.groupby('cat_clean')['value_bil'].sum().sort_values(ascending=False)
        result = result[result > 0.01]  # Filter tiny values
        result = result[~result.index.str.match(
            r'^(Total |Memorandum|.*balance$|^Revenue$|^Expense$)', case=False, na=False
        )]
        
        return {str(k): float(v) for k, v in result.head(n).items() 
                if pd.notna(k) and str(k).strip()}

    receipts = top_map(rec, 20) if not rec.empty else {}
    outlays = top_map(out, 20) if not out.empty else {}
    
    # Explode "Other expense" with Level 3 detail
    if "Other expense" in outlays and outlays["Other expense"] > 0.1:
        other_expense_budget = outlays["Other expense"]
        out_clean = out.copy()
        out_clean['cat_clean'] = out_clean[cat_col].astype(str).str.replace(
            r'\s*\[[\d,\s]+\]', '', regex=True
        ).str.strip()
        out_clean['bracket'] = out_clean[cat_col].astype(str).str.extract(
            r'\[(\d+)\]', expand=False
        ).fillna('')
        
        level3 = out_clean[(out_clean['bracket'].str.len() == 3) & 
                           (out_clean['bracket'].astype(str).str.startswith('28', na=False))].copy()
        
        if not level3.empty:
            level3_agg = level3.groupby('cat_clean')['value_bil'].sum().sort_values(ascending=False)
            level3_agg = level3_agg[level3_agg > 0.01]
            
            if len(level3_agg) > 1:
                del outlays["Other expense"]
                for cat, val in level3_agg.items():
                    if cat and cat.strip():
                        outlays[cat] = float(val)
    
    per = f"Canada federal (annual) REF_DATE={chosen} (StatsCan 10-10-0016-01)"
    return per, receipts, outlays
