"""
Data parsers for Canadian fiscal data sources.

Modules:
- canada_federal: StatsCan federal government data
- quebec: Québec provincial government data
- errors: Custom exceptions
"""

from .canada_federal import parse_statcan_federal_gfs_zip
from .quebec import parse_qc_vol1_csv

__all__ = [
    "parse_statcan_federal_gfs_zip",
    "parse_qc_vol1_csv",
]
