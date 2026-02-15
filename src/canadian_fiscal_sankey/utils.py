"""
Utility functions for data fetching and PDF text extraction.
"""

from pathlib import Path

import requests
from PyPDF2 import PdfReader

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def http_get(url: str, timeout: int = 60) -> bytes:
    """Fetch raw bytes from HTTP(S) URL with standard User-Agent."""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r.content


def download(url: str, dst: Path) -> None:
    """Download file from URL and save to destination."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(http_get(url))


def pdf_text(pdf_path: Path) -> str:
    """Extract all text from PDF file."""
    reader = PdfReader(str(pdf_path))
    out = []
    for p in reader.pages:
        out.append(p.extract_text() or "")
    return "\n".join(out)


def money_to_millions(raw: str) -> float:
    """Convert formatted money string to numeric millions."""
    s = raw.strip()
    s = s.replace("\u2212", "-").replace(",", "").replace(" ", "")
    return float(s)
