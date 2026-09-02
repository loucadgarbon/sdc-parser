"""Static TCL/SDC script analyzer exporting command tables to CSV and Excel."""

__version__ = "1.0.0"

from .analyzer import Analyzer, summarize
from .model import Command, DetailRecord, ParseError, Word
from .parser import normalize_source, parse_script

__all__ = [
    "Analyzer",
    "Command",
    "DetailRecord",
    "ParseError",
    "Word",
    "normalize_source",
    "parse_script",
    "summarize",
]
