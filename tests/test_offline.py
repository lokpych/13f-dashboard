"""Offline checks: no internet needed. Run with:  python3 -m pytest tests -q"""
import math

import pandas as pd

from thirteenf import amendments as amd, checks as chk, claims as clm, compare as cmp


def row(acc, n, cusip, qty, value, issuer="ACME CORP", cls="COM"):
    return {"accession": acc, "row_ordinal": n, "issuer": issuer, "class_title": cls, "cusip": cusip,
            "value_raw": value, "quantity": qty, "qty_type": "SH", "put_call": ""}


def filing(acc, rows, form="13F-HR", amend=None, kind=None, no=None, ts="2024-01-01T00:00:00.000Z",
           conf=False, reason=None):
    return {"accession": acc, "form": form, "report_period": "2023-09-30", "filing_date": ts[:10],
            "acceptance_ts": ts, "is_amendment": amend is not None, "amendment_no": no,
            "amendment_type": kind, "amendment_reason": reason, "confidential_omitted": conf,
            "report_type": "holdings", "rows": rows, "validation": "VERIFIED",
            "parsed_entry_total": len(rows or []), "parsed_value_total": 0}


def test_value_units_change_on_2023_01_03():
    assert cmp.multiplier("2023-01-02") == 1000
    assert cmp.multiplier("2023-01-03") == 1


def test_statuses_and_new_position_has_no_percent():
    p = cmp.group_positions(cmp.normalize([row("A", 1, "111111111", 100, 5), row("A", 2, "222222222", 50, 5)], "2024-01-01"))
    c = cmp.group_positions(cmp.normalize([row("B", 1, "111111111", 150, 9), row("B", 2, "333333333", 70, 7)], "2024-01-01"))
    out = cmp.compare(p, c).set_index("cusip")
    assert out.loc["111111111", "status"] == "Increased" and round(out.loc["111111111", "qty_pct"], 2) == 0.5
    assert out.loc["222222222", "status"] == "No longer reported" and out.loc["222222222", "qty_pct"] == -1.0
    assert out.loc["333333333", "status"] == "Newly reported" and math.isnan(out.loc["333333333", "qty_pct"])


def test_restatement_replaces_and_new_holdings_supplements():
    base = filing("A", [row("A", 1, "111111111", 1, 1)], ts="2023-11-14T00:00:00.000Z")
    restate = filing("B", [row("B", 1, "111111111", 2, 2)], "13F-HR/A", True, "RESTATEMENT", 1, "2023-11-16T00:00:00.000Z")
    extra = filing("C", [row("C", 1, "999999999", 3, 3)], "13F-HR/A", True, "NEW HOLDINGS", 2, "2024-05-15T20:00:00.000Z")
    rows, accs, gaps = amd.effective_disclosure([base, restate, extra])
    assert accs == ["B", "C"] and len(rows) == 2 and gaps == []
    rows, accs, _ = amd.effective_disclosure([base, restate, extra], cutoff="2024-05-14T23:59:59")
    assert accs == ["B"] and len(rows) == 1


def test_unclear_numbering_and_unavailable_amendment_suppress_the_comparison():
    base = filing("A", [row("A", 1, "111111111", 1, 1)])
    skipped = filing("C", [row("C", 1, "999999999", 3, 3)], "13F-HR/A", True, "NEW HOLDINGS", 2)
    rows, accs, gaps = amd.effective_disclosure([base, skipped])
    assert rows == [] and gaps[0]["code"] == "amendment_sequence"
    missing = filing("D", None, "13F-HR/A", True, "NEW HOLDINGS", 1)
    rows, accs, gaps = amd.effective_disclosure([base, missing])
    assert rows == [] and gaps[0]["code"] == "amendment_unavailable"


def test_confidential_gap_clears_only_with_evidence_and_suppresses_new_exit_claims():
    base = filing("A", [row("A", 1, "111111111", 1, 1)], conf=True)
    _, _, gaps = amd.effective_disclosure([base])
    assert [g["code"] for g in gaps] == ["confidential"]
    df = pd.DataFrame({"status": ["Newly reported", "Increased"], "claim_ok": [True, True],
                       "weight_prior": [0.1, 0.1], "weight_cur": [0.1, 0.1], "weight_change_pp": [0.0, 0.0]})
    out, notes = chk.apply_coverage(df, gaps, [])
    assert out["status"].tolist() == [chk.SUPPRESSED, "Increased"] and len(notes) == 1
    assert out["weight_cur"].isna().all()


def test_timeline_marks_late_new_holdings_as_historical_release():
    late = filing("C", [row("C", 1, "9", 3, 3)], "13F-HR/A", True, "NEW HOLDINGS", 1, "2024-05-15T20:00:00.000Z")
    late["filing_date"] = "2024-05-15"
    restate = filing("B", [row("B", 1, "9", 3, 3)], "13F-HR/A", True, "RESTATEMENT", 1, "2023-11-16T20:00:00.000Z")
    t = chk.disclosure_timeline([late, restate]).set_index("accession")
    assert bool(t.loc["C", "historical_release"]) and not bool(t.loc["B", "historical_release"])
    assert t.loc["C", "report_period"] == "2023-09-30"


def test_brief_rejects_unknown_ids_and_missing_required_notes():
    c = [clm.Claim(id="C001", kind="quantity", filer="F", scope="s", periods=("a", "b"), security="X", cusip="1",
                   instrument="SH", direction="Newly reported", prior=0, cur=5, unit="shares", accessions=["0001-01-1"]),
         clm.Claim(id="C002", kind="coverage", filer="F", scope="s", periods=("a", "b"), required=True,
                   text="partial", accessions=["0001-01-1"])]
    assert clm.validate_selection(["C001", "C002"], c)[0]
    ok, why = clm.validate_selection(["C001", "C999"], c)
    assert not ok and why == {"unknown": ["C999"], "missing_required": ["C002"]}
    assert "(+" not in clm.render(c[0], "1") and "(" in clm.render(c[0], "1")
