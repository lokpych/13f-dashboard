"""Project settings. Credentials come from environment variables, never from this file."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "cache"
DB_PATH = ROOT / "data" / "thirteenf.sqlite"

MAX_REQUESTS_PER_SECOND = 5       # shared SEC limiter (SEC allows 10, we stay well under)
MAX_RETRIES = 4                   # bounded retries for transient failures
REQUEST_TIMEOUT = 30              # seconds
DISCOVERY_MAX_AGE = 3600          # refresh filing lists after an hour; documents stay cached

UNIT_CHANGE_DATE = "2023-01-03"   # filings before this date report value in $ thousands
LATE_DAYS = 45                    # a release later than this after period end is "historical"
MAX_BULLETS = 5

# Class labels treated as the same only where this is documented (spec: no guessing).
CLASS_EQUIVALENTS = {"COMMON STOCK": "COM", "COMMON": "COM"}


def user_agent():
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError('Set SEC_USER_AGENT first, e.g. "Your Name you@school.edu". '
                           "The SEC requires every request to identify who is asking.")
    return ua


# Rows to keep from a filer's information table. Used where a parent files the 13F for a manager that only
# files a notice (PIMCO inside Allianz's report). Validation still runs on the full table first.
ROW_SUBSETS = {
    "0001535323": {"tag": "4", "label": "PIMCO rows only (other manager #4)"},
}
