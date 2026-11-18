"""SQLite storage. Raw rows are keyed by (accession, row_ordinal); refresh is atomic and idempotent."""
import json
import sqlite3

import config

FILING_COLS = ["accession", "cik", "report_period", "form", "filing_date", "acceptance_ts",
               "report_type", "is_amendment", "amendment_no", "amendment_type", "amendment_reason",
               "confidential_omitted", "manager_refs", "links", "summary_entry_total",
               "parsed_entry_total", "summary_value_total", "parsed_value_total", "validation",
               "validation_detail", "retrieved_at"]
ROW_COLS = ["accession", "row_ordinal", "issuer", "class_title", "cusip", "figi", "value_raw",
            "quantity", "qty_type", "put_call", "discretion", "other_managers", "vote_sole",
            "vote_shared", "vote_none"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS filings ({", ".join(c + (" TEXT PRIMARY KEY" if c == "accession" else "") for c in FILING_COLS)});
CREATE TABLE IF NOT EXISTS holdings_raw ({", ".join(ROW_COLS)}, PRIMARY KEY (accession, row_ordinal));
"""


def connect(path=None):
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.executescript(SCHEMA)
    return con


def _val(v):
    return json.dumps(v) if isinstance(v, (list, dict)) else v


def save_filing(con, f):
    """Replace one accession (filing record + all its rows) in a single transaction."""
    with con:
        con.execute("DELETE FROM holdings_raw WHERE accession = ?", (f["accession"],))
        con.execute("DELETE FROM filings WHERE accession = ?", (f["accession"],))
        con.execute(f"INSERT INTO filings VALUES ({','.join('?' * len(FILING_COLS))})",
                    [_val(f.get(c)) for c in FILING_COLS])
        for r in f.get("rows") or []:
            con.execute(f"INSERT INTO holdings_raw VALUES ({','.join('?' * len(ROW_COLS))})",
                        [r.get(c) for c in ROW_COLS])


def count_rows(con, accession=None):
    if accession:
        return con.execute("SELECT COUNT(*) FROM holdings_raw WHERE accession = ?", (accession,)).fetchone()[0]
    return con.execute("SELECT COUNT(*) FROM holdings_raw").fetchone()[0]


def fingerprint(con, accession):
    """Hash-like summary of the stored rows, used to prove a refresh left them identical."""
    rows = con.execute("SELECT * FROM holdings_raw WHERE accession = ? ORDER BY row_ordinal",
                       (accession,)).fetchall()
    return hash(tuple(rows))
