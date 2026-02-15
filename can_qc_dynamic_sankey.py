#!/usr/bin/env python3
"""
can_qc_dynamic_sankey.py (v6)

Adds user-selectable periods (instead of always "latest") while keeping:
- CSV/XLS first where available
- PDF fallback where necessary
- downloads + caches sources to ./source_data/

New CLI options
---------------
Canada annual (StatsCan 10-10-0016-01):
  --can-annual-ref-date <REF_DATE>
    Choose a specific REF_DATE value from the StatsCan CSV (e.g., 2023).
    Default: latest REF_DATE in the file.

Canada YTD (Fiscal Monitor):
  --can-fm-year YYYY --can-fm-month MM
    Choose a specific Fiscal Monitor month. Default: latest month discovered.

Québec annual (Vol 1 CSV):
  --qc-annual-year <value>
    Choose a specific year value as stored in the Québec CSV (often numeric or a string).
    Default: latest year value in the file.

Québec YTD (Financial Situation PDF):
  --qc-fin-year YYYY   (or YYYY-YY)
    Choose a specific year token to match in the discovered PDF links.
    Default: latest discovered report.

Tip: list available Canada Fiscal Monitor months with:
  --list-can-fm

"""
from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import pandas as pd
import requests
from PyPDF2 import PdfReader
import plotly.graph_objects as go

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
DEFAULT_SOURCE_DIR = Path("source_data")

CAN_FM_INDEX = "https://www.canada.ca/en/department-finance/services/publications/fiscal-monitor.html"
QC_PUB_INDEX = "https://www.quebec.ca/en/government/departments-agencies/finances/publications"

STATCAN_FED_GFS_ZIP = "https://www150.statcan.gc.ca/n1/en/tbl/csv/10100016-eng.zip"
QC_VOL1_CSV = "https://www.donneesquebec.ca/recherche/dataset/55b05d9f-93d1-450f-a8b9-c761b0c001a8/resource/a6e9462c-d415-4344-b6fe-43271d373b53/download/donnees_ouvertes_vol1_statistiques_24-25.csv"
QC_VOL2_CSV = "https://www.donneesquebec.ca/recherche/dataset/f94cd34c-8202-4cfe-9610-6ae10bf34bc3/resource/11699738-f16e-4b10-90e2-302d8c54128e/download/donnees_ouvertes_vol2_dep_supercat_24-25.csv"


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


def http_get(url: str, timeout: int = 60) -> bytes:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r.content


def download(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(http_get(url))


def pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    out = []
    for p in reader.pages:
        out.append(p.extract_text() or "")
    return "\n".join(out)


def money_to_millions(raw: str) -> float:
    s = raw.strip()
    s = s.replace("\u2212", "-").replace(",", "").replace(" ", "")
    return float(s)


def find_row_keywords(text: str, keywords: List[str], value_pattern: str = r"(-?\d[\d, ]*)") -> float:
    t = text
    k0 = keywords[0]
    for m in re.finditer(re.escape(k0), t, flags=re.IGNORECASE):
        start = max(0, m.start() - 50)
        end = min(len(t), m.start() + 220)
        window = t[start:end]
        if not all(re.search(re.escape(k), window, flags=re.IGNORECASE) for k in keywords):
            continue
        m2 = re.search(value_pattern, window)
        if not m2:
            continue
        return money_to_millions(m2.group(1))
    raise KeyError(f"Could not find row with keywords: {keywords}")


# -------------------------
# Discovery: Canada Fiscal Monitor months
# -------------------------
def discover_fiscal_monitor_pages() -> Dict[Tuple[int, int], str]:
    """
    Returns mapping (YYYY, MM) -> monthly page URL.
    """
    html = http_get(CAN_FM_INDEX).decode("utf-8", errors="ignore")
    pages = re.findall(r'href="([^"]+/fiscal-monitor/\d{4}/\d{2}\.html)"', html, flags=re.IGNORECASE)
    pages = [urljoin(CAN_FM_INDEX, p) for p in pages]
    pages = sorted(set(pages))
    out: Dict[Tuple[int,int], str] = {}
    for p in pages:
        m = re.search(r"/fiscal-monitor/(\d{4})/(\d{2})\.html", p)
        if m:
            out[(int(m.group(1)), int(m.group(2)))] = p
    return out


def fiscal_monitor_pdf_from_month(year: int, month: int) -> str:
    pages = discover_fiscal_monitor_pages()
    if (year, month) not in pages:
        avail = ", ".join([f"{y}-{m:02d}" for (y,m) in sorted(pages.keys())][-24:])
        die(f"Requested Fiscal Monitor {year}-{month:02d} not found. Recent available: {avail}")
    page_url = pages[(year, month)]
    page_html = http_get(page_url).decode("utf-8", errors="ignore")
    pdfs = re.findall(r'href="([^"]+?\.pdf)"', page_html, flags=re.IGNORECASE)
    pdfs = [urljoin(page_url, p) for p in pdfs]
    if not pdfs:
        die(f"No PDF found on Fiscal Monitor page: {page_url}")

    def score(u: str) -> int:
        uu = u.lower()
        return (10 if "eng" in uu else 0) + (5 if "fm" in uu or "rf" in uu else 0)

    return max(pdfs, key=score)


def discover_latest_fiscal_monitor_pdf() -> Tuple[Tuple[int,int], str]:
    pages = discover_fiscal_monitor_pages()
    if not pages:
        die("Could not discover any Fiscal Monitor monthly pages from the index.")
    latest_ym = max(pages.keys())
    return latest_ym, fiscal_monitor_pdf_from_month(*latest_ym)


# -------------------------
# Discovery: Québec Financial Situation PDFs
# -------------------------
def discover_qc_fin_pdfs() -> List[str]:
    html = http_get(QC_PUB_INDEX).decode("utf-8", errors="ignore")
    pdfs = re.findall(r'(https?://[^"\']+?\.pdf)', html, flags=re.IGNORECASE)
    hrefs = re.findall(r'href="([^"]+?\.pdf)"', html, flags=re.IGNORECASE)
    pdfs += [urljoin(QC_PUB_INDEX, h) for h in hrefs]
    pdfs = list(dict.fromkeys(pdfs))

    # Filter likely Financial Situation report
    cand = [p for p in pdfs if ("financial" in p.lower() and "situation" in p.lower()) or ("sifin" in p.lower())]
    return cand if cand else pdfs


def discover_latest_qc_fin_situation_pdf() -> str:
    cand = discover_qc_fin_pdfs()
    if not cand:
        die("Could not find any Québec Financial Situation PDF link on publications page.")

    def score(u: str) -> Tuple[int, int]:
        uu = u.lower()
        year = 0
        m = re.search(r"(20\d{2})", uu)
        if m:
            year = int(m.group(1))
        return (10 if "sifin" in uu else 0, year)

    return max(cand, key=score)


def qc_fin_pdf_for_year(token: str) -> str:
    cand = discover_qc_fin_pdfs()
    if not cand:
        die("Could not find any Québec Financial Situation PDF links.")
    tok = token.lower().strip()
    matches = [u for u in cand if tok in u.lower()]
    if not matches:
        # Also allow matching just the start year (e.g., 2025 matches 2025-26 filenames)
        if re.fullmatch(r"20\d{2}", tok):
            matches = [u for u in cand if tok in u.lower()]
    if not matches:
        recent = ", ".join(cand[:10])
        die(f"No Québec Financial Situation PDF matched token '{token}'. First links seen: {recent}")
    # pick best by year score
    def score(u: str) -> Tuple[int,int]:
        uu = u.lower()
        year = 0
        m = re.search(r"(20\d{2})", uu)
        if m:
            year = int(m.group(1))
        return (10 if "sifin" in uu else 0, year)
    return max(matches, key=score)


# -------------------------
# Fetch sources
# -------------------------
@dataclass
class SourceBundle:
    statcan_zip: Path
    can_fm_pdf: Path
    qc_vol2_csv: Path
    qc_vol1_csv: Path
    qc_fin_pdf: Path
    selected_can_fm: str
    selected_qc_fin: str


def ensure_sources(
    source_dir: Path,
    refresh: bool,
    can_fm_year: Optional[int],
    can_fm_month: Optional[int],
    qc_fin_year_token: Optional[str],
) -> SourceBundle:
    source_dir.mkdir(parents=True, exist_ok=True)

    statcan_zip = source_dir / "statcan_10100016_eng.zip"
    if refresh or not statcan_zip.exists():
        download(STATCAN_FED_GFS_ZIP, statcan_zip)

    # Canada FM selection
    if can_fm_year is not None and can_fm_month is not None:
        can_fm_url = fiscal_monitor_pdf_from_month(can_fm_year, can_fm_month)
        selected_can_fm = f"{can_fm_year}-{can_fm_month:02d}"
    else:
        (ym, can_fm_url) = discover_latest_fiscal_monitor_pdf()
        selected_can_fm = f"{ym[0]}-{ym[1]:02d}"

    can_fm_pdf = source_dir / f"can_fiscal_monitor_{selected_can_fm}.pdf"
    if refresh or not can_fm_pdf.exists():
        download(can_fm_url, can_fm_pdf)

    # Use Volume 2 for detailed ministry breakdown
    qc_vol2_csv = source_dir / "qc_public_accounts_vol2_detailed_latest.csv"
    if refresh or not qc_vol2_csv.exists():
        download(QC_VOL2_CSV, qc_vol2_csv)
    
    # Keep Vol 1 as fallback
    qc_vol1_csv = source_dir / "qc_public_accounts_vol1_latest.csv"
    if refresh or not qc_vol1_csv.exists():
        download(QC_VOL1_CSV, qc_vol1_csv)

    # Québec Financial Situation selection
    if qc_fin_year_token:
        qc_fin_url = qc_fin_pdf_for_year(qc_fin_year_token)
        selected_qc_fin = qc_fin_year_token
    else:
        qc_fin_url = discover_latest_qc_fin_situation_pdf()
        # best effort: extract year
        m = re.search(r"(20\d{2})", qc_fin_url)
        selected_qc_fin = m.group(1) if m else "latest"

    qc_fin_pdf = source_dir / f"qc_financial_situation_{selected_qc_fin}.pdf"
    if refresh or not qc_fin_pdf.exists():
        download(qc_fin_url, qc_fin_pdf)

    return SourceBundle(statcan_zip, can_fm_pdf, qc_vol2_csv, qc_vol1_csv, qc_fin_pdf, selected_can_fm, selected_qc_fin)


# -------------------------
# Parse StatsCan ZIP (Canada annual)
# -------------------------
def _pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def parse_statcan_federal_gfs_zip(zip_path: Path, ref_date_choice: Optional[str]) -> Tuple[str, Dict[str, float], Dict[str, float]]:
    with zipfile.ZipFile(zip_path, "r") as z:
        csv_names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            die(f"No CSV found inside ZIP: {zip_path}")
        csv_name = max(csv_names, key=lambda n: z.getinfo(n).file_size)
        raw = z.read(csv_name)

    df = pd.read_csv(io.BytesIO(raw))
    ref_col = _pick_col(df, ["REF_DATE", "ref_date"])
    val_col = _pick_col(df, ["VALUE", "value"])
    if ref_col is None or val_col is None:
        die(f"StatsCan CSV missing REF_DATE or VALUE. Columns={list(df.columns)}")

    # Choose REF_DATE
    if ref_date_choice:
        # exact string match or numeric match
        choices = df[ref_col].astype(str).unique().tolist()
        if ref_date_choice not in choices:
            # accept integer-like match
            if ref_date_choice.isdigit() and str(int(ref_date_choice)) in choices:
                ref_date_choice = str(int(ref_date_choice))
            else:
                recent = ", ".join(sorted(choices)[-10:])
                die(f"Requested --can-annual-ref-date '{ref_date_choice}' not found. Recent REF_DATE values: {recent}")
        chosen = ref_date_choice
    else:
        chosen = str(df[ref_col].max())

    d = df[df[ref_col].astype(str) == chosen].copy()
    d["value_bil"] = pd.to_numeric(d[val_col], errors="coerce") / 1000.0  # millions -> billions
    d = d.dropna(subset=["value_bil"])

    # Filter for "Transactions and other economic flows" in Display value column
    display_col = _pick_col(df, ["Display value", "Estimates"])
    if display_col and display_col in d.columns:
        d = d[d[display_col].astype(str).str.contains("transaction", case=False, na=False)]

    # Use "Statement of operations and balance sheet" as the category column
    cat_col = _pick_col(df, ["Statement of operations and balance sheet"])
    if cat_col is None:
        # Fallback to other columns
        cat_col = _pick_col(df, ["Display value", "Public sector components"])
    if cat_col is None:
        dim_cols = [c for c in df.columns if c not in (ref_col, val_col, "VECTOR", "COORDINATE", "STATUS", "SYMBOL", "TERMINATED", "DECIMALS", "DGUID", "GEO", "UOM", "UOM_ID", "SCALAR_FACTOR", "SCALAR_ID")]
        cat_col = dim_cols[0] if dim_cols else None
    if cat_col is None:
        die("Could not pick a category column for StatsCan data.")

    # Classify revenue vs expense using bracket codes
    # Revenue categories have brackets starting with [1...], expenses with [2...]
    cat_series = d[cat_col].astype(str)
    d['bracket'] = cat_series.str.extract(r'\[(\d+)\]', expand=False)
    
    rec = d[d['bracket'].str.startswith('1', na=False)].copy()
    out = d[d['bracket'].str.startswith('2', na=False)].copy()

    if rec.empty and out.empty:
        # Fallback to keyword search if bracket approach doesn't work
        rec = d[cat_series.str.contains("revenue|receipts|taxes|contributions", case=False, na=False) & 
                ~cat_series.str.contains("expense|expenditure", case=False, na=False)]
        out = d[cat_series.str.contains("expense|expenditure", case=False, na=False)]
        
    if rec.empty and out.empty:
        out = d

    def top_map(x: pd.DataFrame, n: int = 20, is_expense: bool = False) -> Dict[str, float]:
        # Strategy: Use Level 2 categories (bracket length = 2)
        # These are the main subcategories that sum to the official totals without double-counting
        
        x_clean = x.copy()
        x_clean['cat_clean'] = x_clean[cat_col].astype(str).str.replace(r'\s*\[[\d,\s]+\]', '', regex=True).str.strip()
        x_clean['bracket'] = x_clean[cat_col].astype(str).str.extract(r'\[(\d+)\]', expand=False).fillna('')
        
        # Keep only Level 2 categories (bracket length = 2)
        # These are direct children of Revenue [1] or Expense [2]
        x_level2 = x_clean[x_clean['bracket'].str.len() == 2].copy()
        
        # Aggregate by clean category name
        result = x_level2.groupby('cat_clean')['value_bil'].sum().sort_values(ascending=False)
        
        # Filter out very small values (but Level 2 categories are all significant, so this is a safety net)
        result = result[result > 0.01]  # At least 0.01 billion
        
        # Only remove truly generic summary rows (not actual Level 2 categories like "Taxes" or "Grants, expense")
        result = result[~result.index.str.match(r'^(Total |Memorandum|.*balance$|^Revenue$|^Expense$)', case=False, na=False)]
        
        return {str(k): float(v) for k, v in result.head(n).items() if pd.notna(k) and str(k).strip()}

    receipts = top_map(rec, 20, is_expense=False) if not rec.empty else {}
    outlays = top_map(out, 20, is_expense=True) if not out.empty else {}
    per = f"Canada federal (annual) REF_DATE={chosen} (StatsCan 10-10-0016-01)"
    return per, receipts, outlays


# -------------------------
# Parse Canada Fiscal Monitor PDF (YTD)
# -------------------------
def parse_canada_fm_pdf(text: str, label_suffix: str, relaxed: bool = True) -> Tuple[str, Dict[str, float], Dict[str, float]]:
    per = f"Canada federal (YTD) {label_suffix}".strip()
    rev_map = {
        "Personal income taxes": ["personal", "income", "tax"],
        "Corporate income taxes": ["corporate", "income", "tax"],
        "Non-resident income taxes": ["non", "resident", "income", "tax"],
        "Goods and Services Tax": ["goods", "services", "tax"],
        "Energy taxes": ["energy", "tax"],
        "Customs import duties": ["customs", "import", "duties"],
        "Other excise taxes and duties": ["excise", "duties"],
        "Employment Insurance premiums": ["employment", "insurance", "premiums"],
        "Other revenues": ["other", "revenues"],
    }
    exp_map = {
        "Elderly benefits": ["elderly", "benefits"],
        "Employment Insurance benefits": ["employment", "insurance", "benefits"],
        "Children's benefits": ["children", "benefits"],
        "Canada Health Transfer": ["canada", "health", "transfer"],
        "Canada Social Transfer": ["canada", "social", "transfer"],
        "Equalization": ["equalization"],
        "Territorial Formula Financing": ["territorial", "formula", "financing"],
        "Canada-wide early learning and child care": ["early", "learning", "child", "care"],
        "Canada Community-Building Fund": ["community-building", "fund"],
        "Health agreements with provinces and territories": ["health", "agreements", "provinces"],
        "Other fiscal arrangements": ["other", "fiscal", "arrangements"],
        "Other transfer payments": ["other", "transfer", "payments"],
        "Operating expenses": ["operating", "expenses"],
        "Public debt charges": ["public", "debt", "charges"],
        "Net actuarial losses": ["actuarial", "losses"],
    }
    receipts: Dict[str, float] = {}
    for label, kws in rev_map.items():
        try:
            receipts[label] = find_row_keywords(text, kws) / 1000.0
        except KeyError:
            if not relaxed:
                raise
    outlays: Dict[str, float] = {}
    for label, kws in exp_map.items():
        try:
            outlays[label] = find_row_keywords(text, kws) / 1000.0
        except KeyError:
            if not relaxed:
                raise
    return per, receipts, outlays


# -------------------------
# Parse Québec annual CSV (Vol 1)
# -------------------------
def parse_qc_vol2_csv(path: Path, year_choice: Optional[str]) -> Tuple[str, Dict[str, float], Dict[str, float]]:
    """Parse Quebec Public Accounts Volume 2 - Detailed spending by ministry/portfolio."""
    df = pd.read_csv(path, sep=';', encoding='utf-8')
    
    # Volume 2 has specific columns: Portefeuille (ministry), Montant (amount)
    if 'Portefeuille' not in df.columns or 'Montant' not in df.columns:
        die(f"Québec Vol 2 CSV missing Portefeuille/Montant columns. Columns={list(df.columns)}")
    
    # Clean the amount column: remove spaces and convert to numeric
    df['Montant'] = df['Montant'].astype(str).str.replace(' ', '', regex=False).str.replace(',', '.', regex=False)
    df['Montant'] = pd.to_numeric(df['Montant'], errors='coerce')
    
    # Group by portfolio (ministry) and sum
    spending = df.groupby('Portefeuille')['Montant'].sum().sort_values(ascending=False)
    
    # Convert to billions and get top 12
    spending_billion = spending / 1e9
    top_portfolios = spending_billion.head(12)
    
    per = "Québec annual 2024-2025 (Vol 2 CSV - Detailed by Ministry)"
    
    # Volume 2 only has expenses (no revenue breakdown in this file)
    receipts = {}
    outlays = {str(k): float(v) for k, v in top_portfolios.items() if pd.notna(k) and str(k).strip()}
    
    return per, receipts, outlays


def parse_qc_hybrid_csv(vol2_path: Path, vol1_path: Path, year_choice: Optional[str]) -> Tuple[str, Dict[str, float], Dict[str, float]]:
    """Parse Quebec: receipts from Vol 1, outlays as Vol 1 majors + Vol 2 detailed (replacing "Autres portefeuilles")."""
    # Get data from Vol 1
    df1 = pd.read_csv(vol1_path, sep=';', encoding='utf-8')
    df1['Annee'] = df1['Annee'].str.strip()
    df1['Montant'] = df1['Montant'].astype(str).str.replace(' ', '', regex=False).str.replace(',', '.', regex=False)
    df1['Montant'] = pd.to_numeric(df1['Montant'], errors='coerce')
    
    # Find year
    all_years = sorted(df1['Annee'].unique())
    if year_choice:
        chosen = year_choice if year_choice in all_years else str(max(all_years))
    else:
        chosen = str(max(all_years))
    
    d1 = df1[df1['Annee'] == chosen].copy()
    
    # Extract receipts (all revenue categories)
    receipts = {}
    rev_rows = d1[d1['REGRP_Sommaire'].str.contains('revenu', case=False, na=False)]
    for _, row in rev_rows.iterrows():
        cat = str(row['REGRP_Nom']).strip()
        val = row['Montant']
        if pd.notna(val) and val > 0:
            receipts[cat] = float(val / 1e9)  # Convert to billions
    
    # Get expenses from Vol 1, but EXCLUDE "Autres portefeuilles" (will replace with Vol 2)
    outlays = {}
    exp_rows = d1[d1['REGRP_Sommaire'].str.contains('dépense', case=False, na=False)]
    autres_budget = 0.0  # Track what "Autres portefeuilles" was budgeted at
    
    for _, row in exp_rows.iterrows():
        cat = str(row['REGRP_Nom']).strip()
        val = row['Montant']
        if pd.notna(val) and val > 0:
            if 'autres portefeuilles' in cat.lower():
                autres_budget = float(val / 1e9)  # Save the budget for "Autres portefeuilles"
            else:
                outlays[cat] = float(val / 1e9)  # Keep major categories
    
    # Get detailed ministries from Vol 2, but cap at what "Autres portefeuilles" was
    df2 = pd.read_csv(vol2_path, sep=';', encoding='utf-8')
    if 'Portefeuille' in df2.columns and 'Montant' in df2.columns:
        df2['Montant'] = df2['Montant'].astype(str).str.replace(' ', '', regex=False).str.replace(',', '.', regex=False)
        df2['Montant'] = pd.to_numeric(df2['Montant'], errors='coerce')
        
        ministries = df2.groupby('Portefeuille')['Montant'].sum().sort_values(ascending=False)
        
        # Add ministries until we reach the "Autres portefeuilles" budget or run out
        cumsum = 0.0
        for ministry, amount in ministries.items():
            amount_b = float(amount / 1e9)
            if cumsum + amount_b > autres_budget * 1.05:  # Stop if we exceed budget by 5%
                break
            if pd.notna(ministry) and str(ministry).strip():
                outlays[str(ministry).strip()] = amount_b
                cumsum += amount_b
    
    per = f"Québec annual {chosen} (Vol 1 receipts + Vol 2 detailed outlays)"
    return per, receipts, outlays


def parse_qc_vol1_csv(path: Path, year_choice: Optional[str]) -> Tuple[str, Dict[str, float], Dict[str, float]]:
    """Parse Quebec Vol 1 with "Autres portefeuilles" exploded using Vol 2 detailed breakdown."""
    df = pd.read_csv(path, sep=';', encoding='utf-8')
    value_col = None
    for c in df.columns:
        if c.lower() in ("valeur", "value", "montant", "amount"):
            value_col = c; break
    if value_col is None:
        for c in df.columns:
            if "val" in c.lower():
                value_col = c; break
    if value_col is None:
        die(f"Could not detect value column in Québec CSV. Columns={list(df.columns)}")
    
    # Clean the amount column: remove spaces and convert to numeric
    df[value_col] = df[value_col].astype(str).str.replace(' ', '', regex=False).str.replace(',', '.', regex=False)
    df[value_col] = pd.to_numeric(df[value_col], errors='coerce')

    year_col = "Annee"
    if year_col not in df.columns:
        die("Could not detect year column in Québec CSV")
    
    df[year_col] = df[year_col].str.strip()

    all_years = df[year_col].astype(str).unique().tolist()
    if year_choice:
        if year_choice not in all_years:
            recent = ", ".join(sorted(all_years)[-10:])
            die(f"Requested --qc-annual-year '{year_choice}' not found. Recent year values: {recent}")
        chosen = year_choice
    else:
        chosen = str(df[year_col].max())

    per = f"Québec annual {chosen} (Vol 1 CSV)"

    som_col = "REGRP_Sommaire"
    nom_col = "REGRP_Nom"
    if som_col not in df.columns or nom_col not in df.columns:
        die("Québec CSV missing REGRP_Sommaire/REGRP_Nom columns")

    d = df[df[year_col].astype(str) == chosen].copy()
    rec = d[d[som_col].str.contains("revenu", case=False, na=False)]
    out = d[d[som_col].str.contains("dépense|depense", case=False, na=False)]

    def agg_top(x: pd.DataFrame, top_n: int = 12) -> Dict[str, float]:
        s = (x.groupby(nom_col)[value_col].sum().sort_values(ascending=False))
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
    outlays_vol1  = agg_top(out, 12) if not out.empty else {}
    
    # Now try to explode "Autres portefeuilles" with Vol 2 data
    outlays = {}
    autres_budget = 0.0
    
    for cat, val in outlays_vol1.items():
        if 'autres portefeuilles' in cat.lower():
            autres_budget = val
        else:
            outlays[cat] = val  # Keep major categories
    
    # Try to add Vol 2 breakdown to replace "Autres portefeuilles"
    try:
        import os
        vol2_path = Path(path.parent) / "qc_public_accounts_vol2_detailed_latest.csv"
        if vol2_path.exists():
            df2 = pd.read_csv(vol2_path, sep=';', encoding='utf-8')
            if 'Portefeuille' in df2.columns and 'Montant' in df2.columns:
                df2['Montant'] = df2['Montant'].astype(str).str.replace(' ', '', regex=False).str.replace(',', '.', regex=False)
                df2['Montant'] = pd.to_numeric(df2['Montant'], errors='coerce')
                
                ministries = df2.groupby('Portefeuille')['Montant'].sum().sort_values(ascending=False)
                
                # Ministries that are NOT major categories (exclude those already shown separately)
                major_categories = ['Santé', 'Éducation', 'Enseignement', 'Service de la dette']
                filtered_ministries = []
                for ministry, amount in ministries.items():
                    # Skip if it matches a major category
                    if not any(major in str(ministry) for major in major_categories):
                        filtered_ministries.append((str(ministry).strip(), float(amount / 1e9)))
                
                # Scale filtered ministries to fit within "Autres portefeuilles" budget
                if filtered_ministries:
                    total_filtered = sum(v for _, v in filtered_ministries)
                    if total_filtered > 0:
                        scale_factor = autres_budget / total_filtered
                        for ministry, amount in filtered_ministries:
                            outlays[ministry] = amount * scale_factor
    except Exception as e:
        # If Vol 2 processing fails, just keep Vol 1 "Autres" as-is with fallback
        outlays["Autres portefeuilles"] = autres_budget
    
    # If no Vol 2 breakdown was added, show "Autres portefeuilles"
    if autres_budget > 0 and not any('portefeuille' not in cat.lower() and cat in outlays for cat in outlays_vol1.keys() if 'autres' not in cat.lower()):
        # Check if we actually added ministries
        has_other_than_major = False
        for cat in outlays:
            if not any(major in cat for major in ['Santé', 'Éducation', 'Enseignement', 'Service de la dette']):
                has_other_than_major = True
                break
        
        if not has_other_than_major:
            outlays["Autres portefeuilles"] = autres_budget
    
    return per, receipts, outlays


# -------------------------
# Parse Québec Financial Situation PDF (YTD)
# -------------------------
def parse_qc_fin_pdf(text: str, label_suffix: str, relaxed: bool = True) -> Tuple[str, Dict[str, float], Dict[str, float]]:
    per = f"Québec (YTD) {label_suffix}".strip()

    receipts: Dict[str, float] = {}
    for label, kws in [
        ("Own-source revenue", ["own-source", "revenue"]),
        ("Federal transfers", ["federal", "transfers"]),
    ]:
        try:
            receipts[label] = find_row_keywords(text, kws) / 1000.0
        except KeyError:
            if not relaxed:
                raise

    def find_bil_kw(kws: List[str]) -> float:
        t = text
        k0 = kws[0]
        for m0 in re.finditer(re.escape(k0), t, flags=re.IGNORECASE):
            start = max(0, m0.start() - 50)
            end = min(len(t), m0.start() + 260)
            window = t[start:end]
            if not all(re.search(re.escape(k), window, flags=re.IGNORECASE) for k in kws):
                continue
            m1 = re.search(r"(\d+(?:\.\d+)?)\s+billion", window, flags=re.IGNORECASE)
            if m1:
                return float(m1.group(1))
        raise KeyError(f"Could not find billions row with keywords: {kws}")

    out_map = {
        "Health & Social Services": ["santé", "services", "sociaux"],
        "Education": ["éducation"],
        "Higher education": ["enseignement", "supérieur"],
        "Family": ["famille"],
        "Transport": ["transports", "mobilité"],
        "Employment & Social solidarity": ["emploi", "solidarité"],
        "Municipal affairs & Housing": ["affaires", "municipales", "habitation"],
        "Economy/Innovation/Energy": ["économie", "innovation", "énergie"],
        "Environment/Climate/Wildlife/Parks": ["environnement", "changements", "climatiques"],
        "Other portfolios": ["other", "portfolios"],
        "Debt service": ["debt", "service"],
    }
    outlays: Dict[str, float] = {}
    for label, kws in out_map.items():
        try:
            outlays[label] = find_bil_kw(kws)
        except KeyError:
            if not relaxed:
                raise
    return per, receipts, outlays


def sankey_figure(title: str, receipts: Dict[str, float], outlays: Dict[str, float], fiscal_period: str = ""):
    total_r = sum(receipts.values())
    total_o = sum(outlays.values())
    balance = total_r - total_o
    deficit = -balance if balance < 0 else 0.0
    surplus = balance if balance > 0 else 0.0

    nodes: List[str] = []
    node_colors: List[str] = []
    idx: Dict[str, int] = {}

    def add_node(name: str, color: str = "lightgray") -> int:
        if name in idx:
            return idx[name]
        idx[name] = len(nodes)
        nodes.append(name)
        node_colors.append(color)
        return idx[name]

    # Central node with totals displayed
    total_node = add_node(f"<b>Total Receipts</b><br>${total_r:.1f}B<br><br><b>Total Outlays</b><br>${total_o:.1f}B", "#FFE5B4")
    
    src: List[int] = []
    tgt: List[int] = []
    val: List[float] = []
    colors: List[str] = []
    labels: List[str] = []

    # Receipts flow into total (green shades)
    receipt_colors = ["#90EE90", "#7CCD7C", "#68B068", "#54A354", "#408940", "#2C6F2C"]
    for i, (k, v) in enumerate(receipts.items()):
        rn = add_node(k, receipt_colors[i % len(receipt_colors)])
        src.append(rn)
        tgt.append(total_node)
        val.append(v)
        colors.append(receipt_colors[i % len(receipt_colors)])
        labels.append(f"${v:.1f}B")

    # If deficit, add it to receipts side to balance the central node
    if deficit > 0.01:
        dn = add_node("<b>Deficit</b>", "#FF6B6B")
        src.append(dn)
        tgt.append(total_node)
        val.append(deficit)
        colors.append("#FF6B6B")
        labels.append(f"${deficit:.1f}B")

    # Outlays flow out of total (teal/blue shades)
    outlay_colors = ["#4682B4", "#5F9EA0", "#48D1CC", "#20B2AA", "#008B8B", "#00CED1", "#4169E1", "#6495ED"]
    for i, (k, v) in enumerate(outlays.items()):
        on = add_node(k, outlay_colors[i % len(outlay_colors)])
        src.append(total_node)
        tgt.append(on)
        val.append(v)
        colors.append(outlay_colors[i % len(outlay_colors)])
        labels.append(f"${v:.1f}B")

    # If surplus, show it flowing out
    if surplus > 0.01:
        sn = add_node("<b>Surplus</b>", "#90EE90")
        src.append(total_node)
        tgt.append(sn)
        val.append(surplus)
        colors.append("#90EE90")
        labels.append(f"${surplus:.1f}B")

    fig = go.Figure(data=[go.Sankey(
        arrangement="snap",
        node=dict(
            pad=20,
            thickness=20,
            line=dict(color="white", width=2),
            label=nodes,
            color=node_colors,
        ),
        link=dict(
            source=src,
            target=tgt,
            value=val,
            color=colors,
            label=labels,
        ),
    )])
    
    # Add main title (without fiscal period to keep short)
    main_title = title
    
    # Add subtitle with totals and deficit/surplus
    subtitle = f"Total Receipts: ${total_r:.1f}B | Total Outlays: ${total_o:.1f}B"
    if deficit > 0.01:
        subtitle += f" | Deficit: ${deficit:.1f}B"
    elif surplus > 0.01:
        subtitle += f" | Surplus: ${surplus:.1f}B"
    
    # Don't use HTML tags in title to avoid rendering issues in PNG exports
    fig.update_layout(
        title_text=f"{main_title} — {subtitle}",
        title_font_size=13,
        font_size=11,
        height=700,
        margin=dict(l=20, r=20, t=140, b=20),
    )
    
    # Add fiscal period as a separate annotation at the very top for visibility
    if fiscal_period:
        fig.add_annotation(
            text=f"Data Period: {fiscal_period}",
            xref="paper", yref="paper",
            x=0.5, y=1.08,
            showarrow=False,
            font=dict(size=12, color="#444444"),
            xanchor="center", yanchor="top"
        )
    
    return fig


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR))
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--write-csv", default="")
    ap.add_argument("--dump-text", action="store_true")
    ap.add_argument("--strict", action="store_true", help="Fail if a PDF item is missing. Default relaxed skip.")

    # New selection options
    ap.add_argument("--can-annual-ref-date", default="", help="StatsCan REF_DATE to use for Canada annual (e.g., 2023). Default latest.")
    ap.add_argument("--can-fm-year", type=int, default=None, help="Fiscal Monitor year (YYYY)")
    ap.add_argument("--can-fm-month", type=int, default=None, help="Fiscal Monitor month (MM)")
    ap.add_argument("--qc-annual-year", default="", help="Québec CSV year value to use (as stored). Default latest.")
    ap.add_argument("--qc-fin-year", default="", help="Token to match in Québec Financial Situation PDF link (e.g., 2025 or 2025-26). Default latest.")
    ap.add_argument("--list-can-fm", action="store_true", help="List available Fiscal Monitor months found on the index page and exit.")
    ap.add_argument("--include-ytd", action="store_true", help="Include YTD (year-to-date) charts from PDFs. Default: only generate annual charts with aggregated data.")

    args = ap.parse_args()
    if args.list_can_fm:
        pages = discover_fiscal_monitor_pages()
        if not pages:
            die("No Fiscal Monitor months found.")
        for (y,m) in sorted(pages.keys()):
            print(f"{y}-{m:02d}")
        return 0

    if (args.can_fm_year is None) ^ (args.can_fm_month is None):
        die("Use both --can-fm-year and --can-fm-month, or neither for latest.")

    relaxed = not args.strict
    source_dir = Path(args.source_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Conditionally fetch YTD sources only if requested
    if args.include_ytd:
        bundle = ensure_sources(
            source_dir=source_dir,
            refresh=args.refresh,
            can_fm_year=args.can_fm_year,
            can_fm_month=args.can_fm_month,
            qc_fin_year_token=args.qc_fin_year or None,
        )
    else:
        # Only fetch annual data sources (skip YTD PDFs)
        from dataclasses import dataclass
        statcan_zip = source_dir / "statcan_10100016_eng.zip"
        if args.refresh or not statcan_zip.exists():
            source_dir.mkdir(parents=True, exist_ok=True)
            download(STATCAN_FED_GFS_ZIP, statcan_zip)
        
        # Use Volume 2 for detailed ministry breakdown
        qc_vol2_csv = source_dir / "qc_public_accounts_vol2_detailed_latest.csv"
        if args.refresh or not qc_vol2_csv.exists():
            source_dir.mkdir(parents=True, exist_ok=True)
            download(QC_VOL2_CSV, qc_vol2_csv)
        
        # Also download Vol 1 for receipts
        qc_vol1_csv = source_dir / "qc_public_accounts_vol1_latest.csv"
        if args.refresh or not qc_vol1_csv.exists():
            source_dir.mkdir(parents=True, exist_ok=True)
            download(QC_VOL1_CSV, qc_vol1_csv)
        
        @dataclass
        class MinimalBundle:
            statcan_zip: Path
            qc_vol2_csv: Path
            qc_vol1_csv: Path
        
        bundle = MinimalBundle(statcan_zip, qc_vol2_csv, qc_vol1_csv)

    can_ann_period, can_ann_rec, can_ann_out = parse_statcan_federal_gfs_zip(
        bundle.statcan_zip,
        ref_date_choice=args.can_annual_ref_date or None,
    )

    # Only parse YTD data if requested
    if args.include_ytd:
        can_fm_text = pdf_text(bundle.can_fm_pdf)
        if args.dump_text:
            (out_dir/"can_fm_text.txt").write_text(can_fm_text, encoding="utf-8")
        can_ytd_period, can_ytd_rec, can_ytd_out = parse_canada_fm_pdf(
            can_fm_text, label_suffix=bundle.selected_can_fm, relaxed=relaxed
        )
    else:
        can_ytd_period, can_ytd_rec, can_ytd_out = None, {}, {}

    # Use Vol 1 data (official aggregated source with correct totals)
    qc_ann_period, qc_ann_rec, qc_ann_out = parse_qc_vol1_csv(
        bundle.qc_vol1_csv, year_choice=args.qc_annual_year or None
    )

    # Only parse YTD data if requested
    if args.include_ytd:
        qc_fin_text = pdf_text(bundle.qc_fin_pdf)
        if args.dump_text:
            (out_dir/"qc_fin_text.txt").write_text(qc_fin_text, encoding="utf-8")
        qc_ytd_period, qc_ytd_rec, qc_ytd_out = parse_qc_fin_pdf(
            qc_fin_text, label_suffix=bundle.selected_qc_fin, relaxed=relaxed
        )
    else:
        qc_ytd_period, qc_ytd_rec, qc_ytd_out = None, {}, {}

    rows = []
    def add_block(gov, period, rec, out, source):
        for k,v in rec.items():
            rows.append({"government": gov, "period": period, "kind": "receipt", "category": k, "amount": float(v), "unit": "CAD_billion", "source": source})
        for k,v in out.items():
            rows.append({"government": gov, "period": period, "kind": "outlay", "category": k, "amount": float(v), "unit": "CAD_billion", "source": source})

    add_block("Canada (federal)", can_ann_period, can_ann_rec, can_ann_out, "StatsCan 10-10-0016-01 (ZIP/CSV)")
    if args.include_ytd and can_ytd_period:
        add_block("Canada (federal)", can_ytd_period, can_ytd_rec, can_ytd_out, "Finance Canada Fiscal Monitor (PDF)")
    
    add_block("Québec", qc_ann_period, qc_ann_rec, qc_ann_out, "Données Québec Vol 1 (CSV)")
    if args.include_ytd and qc_ytd_period:
        add_block("Québec", qc_ytd_period, qc_ytd_rec, qc_ytd_out, "Québec Financial Situation (PDF)")

    df = pd.DataFrame(rows)
    if args.write_csv:
        out_csv = Path(args.write_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_csv, index=False)

    # Generate charts based on flag
    if args.include_ytd and can_ytd_period and qc_ytd_period:
        charts = [
            ("Canada (federal)", can_ann_period, can_ann_rec, can_ann_out),
            ("Canada (federal)", can_ytd_period, can_ytd_rec, can_ytd_out),
            ("Québec", qc_ann_period, qc_ann_rec, qc_ann_out),
            ("Québec", qc_ytd_period, qc_ytd_rec, qc_ytd_out),
        ]
    else:
        # Default: One aggregated annual chart per government
        charts = [
            ("Canada (federal)", can_ann_period, can_ann_rec, can_ann_out),
            ("Québec", qc_ann_period, qc_ann_rec, qc_ann_out),
        ]
    
    print(f"Generating {len(charts)} Sankey diagram(s)...")
    
    for gov, per, rec, out in charts:
        # Extract fiscal year/period from the period string
        import re
        year_match = re.search(r'(\d{4}-\d{2,4}|\d{4})', per)
        fiscal_year = year_match.group(1) if year_match else "Unknown"
        
        # Format title with fiscal year (the actual reporting period)
        title_base = f"{gov} — Fiscal Year {fiscal_year}: {per} (CAD $B)"
        
        # Pass both title and fiscal period separately for better display
        fig = sankey_figure(title_base, rec, out, fiscal_period=f"Fiscal Year {fiscal_year} ({per})")
        slug = f"{gov}_{per}".replace(" ", "_").replace("/", "_").replace("—","-").replace("–","-").replace("(","").replace(")","")
        html = out_dir / f"sankey_{slug}.html"
        fig.write_html(str(html), include_plotlyjs="cdn")
        try:
            fig.write_image(str(html.with_suffix(".png")), scale=2)
        except Exception:
            pass

    summary = (
        df.groupby(["government","period","kind"], as_index=False)["amount"].sum()
          .pivot_table(index=["government","period"], columns="kind", values="amount", aggfunc="sum")
          .reset_index()
    )
    summary["deficit_or_surplus"] = summary.get("receipt", 0) - summary.get("outlay", 0)
    summary.to_csv(out_dir / "summary_totals.csv", index=False)

    print("Done.")
    print(f"Sources saved in: {source_dir}")
    print(f"Outputs saved in: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
