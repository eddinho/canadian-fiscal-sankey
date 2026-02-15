"""
Parser for Québec provincial government financial data.

Data sources:
- Volume 1: Aggregated receipts and outlays by category
- Volume 2: Detailed ministry/portfolio breakdown (used to explode "Autres portefeuilles")
"""

from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd


def parse_qc_vol1_csv(
    path: Path, 
    year_choice: Optional[str] = None
) -> Tuple[str, Dict[str, float], Dict[str, float]]:
    """
    Parse Québec Public Accounts Volume 1 (aggregated view).
    
    Attempts to explode "Autres portefeuilles" (Other portfolios) using Volume 2 data
    if available in the same directory.
    
    Args:
        path: Path to Québec Vol 1 CSV file
        year_choice: Specific fiscal year to use. If None, uses latest available.
    
    Returns:
        Tuple of (period_label, receipts_dict, outlays_dict)
        Values are in billions (CAD)
    """
    df = pd.read_csv(path, sep=';', encoding='utf-8')
    
    # Detect value column
    value_col = None
    for c in df.columns:
        if c.lower() in ("valeur", "value", "montant", "amount"):
            value_col = c
            break
    if value_col is None:
        for c in df.columns:
            if "val" in c.lower():
                value_col = c
                break
    if value_col is None:
        raise ValueError(f"Could not detect value column in Québec CSV. Columns={list(df.columns)}")
    
    # Clean amount column
    df[value_col] = df[value_col].astype(str).str.replace(' ', '', regex=False).str.replace(',', '.', regex=False)
    df[value_col] = pd.to_numeric(df[value_col], errors='coerce')

    # Detect and validate year column
    year_col = "Annee"
    if year_col not in df.columns:
        raise ValueError("Could not detect year column in Québec CSV")
    
    df[year_col] = df[year_col].str.strip()

    # Choose fiscal year
    all_years = df[year_col].astype(str).unique().tolist()
    if year_choice:
        if year_choice not in all_years:
            recent = ", ".join(sorted(all_years)[-10:])
            raise ValueError(
                f"Requested --qc-annual-year '{year_choice}' not found. "
                f"Recent year values: {recent}"
            )
        chosen = year_choice
    else:
        chosen = str(df[year_col].max())

    per = f"Québec annual {chosen} (Vol 1 CSV)"

    # Find category columns
    som_col = "REGRP_Sommaire"
    nom_col = "REGRP_Nom"
    if som_col not in df.columns or nom_col not in df.columns:
        raise ValueError("Québec CSV missing REGRP_Sommaire/REGRP_Nom columns")

    # Filter by year and split revenue/expense
    d = df[df[year_col].astype(str) == chosen].copy()
    rec = d[d[som_col].str.contains("revenu", case=False, na=False)]
    out = d[d[som_col].str.contains("dépense|depense", case=False, na=False)]

    def agg_top(x: pd.DataFrame, top_n: int = 12) -> Dict[str, float]:
        """Aggregate by category name and return top N."""
        s = x.groupby(nom_col)[value_col].sum().sort_values(ascending=False)
        total = float(s.sum())
        if total > 1e11:
            scale = 1e9
        elif total > 1e8:
            scale = 1e3
        else:
            scale = 1.0
        ss = (s / scale).head(top_n)
        return {str(k): float(v) for k, v in ss.items() if pd.notna(k) and str(k).strip()}

    receipts = agg_top(rec, 12) if not rec.empty else {}
    outlays_vol1 = agg_top(out, 12) if not out.empty else {}
    
    # Try to explode "Autres portefeuilles" using Vol 2
    outlays = {}
    autres_budget = 0.0
    
    for cat, val in outlays_vol1.items():
        if 'autres portefeuilles' in cat.lower():
            autres_budget = val
        else:
            outlays[cat] = val

    # Attempt Vol 2 enrichment
    try:
        vol2_path = Path(path.parent) / "qc_public_accounts_vol2_detailed_latest.csv"
        if vol2_path.exists():
            df2 = pd.read_csv(vol2_path, sep=';', encoding='utf-8')
            if 'Portefeuille' in df2.columns and 'Montant' in df2.columns:
                df2['Montant'] = df2['Montant'].astype(str).str.replace(
                    ' ', '', regex=False
                ).str.replace(',', '.', regex=False)
                df2['Montant'] = pd.to_numeric(df2['Montant'], errors='coerce')
                
                ministries = df2.groupby('Portefeuille')['Montant'].sum().sort_values(ascending=False)
                
                major_categories = ['Santé', 'Éducation', 'Enseignement', 'Service de la dette']
                filtered_ministries = []
                for ministry, amount in ministries.items():
                    if not any(major in str(ministry) for major in major_categories):
                        filtered_ministries.append((str(ministry).strip(), float(amount / 1e9)))
                
                # Scale to fit "Autres portefeuilles" budget
                if filtered_ministries:
                    total_filtered = sum(v for _, v in filtered_ministries)
                    if total_filtered > 0:
                        scale_factor = autres_budget / total_filtered
                        for ministry, amount in filtered_ministries:
                            outlays[ministry] = amount * scale_factor
    except Exception:
        # If Vol 2 fails, keep "Autres portefeuilles" from Vol 1
        if autres_budget > 0:
            outlays["Autres portefeuilles"] = autres_budget
    
    # Fallback: ensure "Autres portefeuilles" is shown if no Vol 2 enrichment happened
    if autres_budget > 0 and not any(
        'portefeuille' not in cat.lower() for cat in outlays
        if cat not in outlays_vol1 or 'autres' not in cat.lower()
    ):
        has_other = any(
            not any(major in cat for major in ['Santé', 'Éducation', 'Enseignement', 'Service de la dette'])
            for cat in outlays
        )
        if not has_other:
            outlays["Autres portefeuilles"] = autres_budget
    
    return per, receipts, outlays
