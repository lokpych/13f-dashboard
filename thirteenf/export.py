"""Excel export: Summary, Holdings, Filing Detail and Sources sheets."""
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER = PatternFill("solid", fgColor="1F3864")
TEXT_COLS = {"cusip", "cik", "accession", "class_norm", "put_call", "qty_type"}


def _write(ws, df):
    ws.append(list(df.columns))
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), HEADER
    for row in df.itertuples(index=False):
        ws.append([None if (isinstance(v, float) and pd.isna(v)) else v for v in row])
    for j, col in enumerate(df.columns, start=1):
        letter = get_column_letter(j)
        ws.column_dimensions[letter].width = max(12, min(45, len(str(col)) + 4))
        for cell in ws[letter][1:]:
            if col in TEXT_COLS:
                cell.number_format = "@"
                cell.value = None if cell.value is None else str(cell.value)
            elif col.startswith("value") or col == "value_change":
                cell.number_format = "#,##0"
            elif col.startswith("weight") and not col.endswith("_pp"):
                cell.number_format = "0.00%"
            elif col == "qty_pct":
                cell.number_format = "0.0%"
            elif col.startswith("quantity") or col == "qty_change":
                cell.number_format = "#,##0"
    ws.freeze_panes = "A2"


def link(acc, cik):
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{acc.replace('-', '')}/{acc}-index.htm")


def export(path, *, filer, cik, scope, periods, cutoff, holdings, filings,
           bullets, coverage_notes, filters="none"):
    """filings: list of load_filing() dicts that contributed to this report."""
    wb = Workbook()

    # --- Summary
    s = wb.active
    s.title = "Summary"
    for row in [("Filer", filer), ("CIK", str(cik).zfill(10)), ("Reporting scope", scope),
                ("Prior period", periods[0]), ("Current period", periods[1]),
                ("Public-as-of cutoff", cutoff or "Latest public version"),
                ("Active filters", filters),
                ("Weight label", "Share of reported 13F value"), ("", ""),
                ("Brief", "")]:
        s.append(row)
    for b in bullets:
        s.append(("", b))
    s.append(("", ""))
    s.append(("Coverage notes", "; ".join(coverage_notes) or "None: both quarters verified"))
    s.append(("13F limits", "Covers reportable long positions at quarter end; excludes "
              "shorts; may omit small or confidential holdings. Not trade dates."))
    s["B2"].number_format = "@"
    s.column_dimensions["A"].width, s.column_dimensions["B"].width = 22, 120
    for r in range(1, s.max_row + 1):
        s.cell(r, 1).font = Font(bold=True)

    # --- Holdings (exactly the filtered comparison table)
    _write(wb.create_sheet("Holdings"), holdings.reset_index(drop=True))

    # --- Filing Detail (original contributing rows)
    raw = pd.concat([pd.DataFrame(f["rows"]).assign(accession=f["accession"],
                                                     report_period=f["report_period"])
                     for f in filings if f.get("rows")], ignore_index=True)
    lead = ["accession", "report_period", "row_ordinal"]
    _write(wb.create_sheet("Filing Detail"), raw[lead + [c for c in raw.columns if c not in lead]])

    # --- Sources (one record per accession)
    src = pd.DataFrame([{
        "accession": f["accession"], "cik": str(cik).zfill(10), "form": f.get("form"),
        "report_period": f["report_period"], "filing_date": f.get("filing_date"),
        "acceptance_ts": f.get("acceptance_ts"), "validation": f["validation"],
        "summary_entry_total": f["summary_entry_total"], "parsed_entry_total": f["parsed_entry_total"],
        "value_summary_total": f["summary_value_total"], "value_parsed_total": f["parsed_value_total"],
        "retrieved_at": f.get("retrieved_at"), "link": link(f["accession"], cik),
    } for f in filings])
    ws = wb.create_sheet("Sources")
    _write(ws, src)
    link_col = list(src.columns).index("link") + 1
    for r in range(2, ws.max_row + 1):
        c = ws.cell(r, link_col)
        c.hyperlink, c.font = c.value, Font(color="0563C1", underline="single")

    wb.save(path)
    return raw


def reconcile(holdings, raw, multipliers):
    """Grouped figures must equal the sum of their source rows."""
    raw = raw.copy()
    raw["value_usd"] = raw["value_raw"] * raw["accession"].map(multipliers)
    out = {}
    for acc in raw["accession"].unique():
        out[acc] = int(raw.loc[raw["accession"] == acc, "value_usd"].sum())
    return out
