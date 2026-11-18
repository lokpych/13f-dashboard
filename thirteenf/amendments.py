"""Filing discovery and the effective disclosure for one report period."""
import pandas as pd

THIRTEENF_FORMS = ("13F-HR", "13F-HR/A", "13F-NT", "13F-NT/A")
COLS = ["accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime", "primaryDocument"]


def thirteenf_filings(subs, client=None):
    """All 13F filings for a filer: the 'recent' block plus every historical file it points to."""
    blocks = [subs["filings"]["recent"]]
    if client is not None:
        for ref in subs["filings"].get("files", []):
            url = f"https://data.sec.gov/submissions/{ref['name']}"
            blocks.append(client.get_json(url)[0])
    frames = [pd.DataFrame({c: b.get(c, []) for c in COLS}) for b in blocks]
    df = pd.concat(frames, ignore_index=True)
    df = df[df["form"].isin(THIRTEENF_FORMS)].drop_duplicates("accessionNumber")
    return df.sort_values("acceptanceDateTime").reset_index(drop=True)


def _gap(code, message, partial, accessions=()):
    return {"code": code, "message": message, "partial": partial, "accessions": list(accessions)}


def _public(f, cutoff):
    if cutoff is None:
        return True
    return (f.get("acceptance_ts") or "")[:19] <= cutoff[:19]


def effective_disclosure(filings, cutoff=None):
    """Return (rows, contributing accessions, gaps) for one report period.

    A restatement replaces the table; a new-holdings amendment adds to it. Only filings accepted
    by `cutoff` (an ISO timestamp) are used. Unclear numbering or a missing base filing gives no
    rows and a gap, so dependent comparisons are suppressed instead of guessed.
    """
    gaps = []
    public = sorted((f for f in filings if _public(f, cutoff)), key=lambda f: f.get("acceptance_ts") or "")
    period = filings[0].get("report_period") if filings else "the requested period"
    bases = [f for f in public if not f.get("is_amendment")]
    amends = [f for f in public if f.get("is_amendment")]

    if not bases:
        gaps.append(_gap("missing_base", f"No original 13F filing for {period} was public"
                         + (f" by {cutoff[:10]}" if cutoff else "") + ".", True))
        return [], [], gaps
    base = bases[-1]
    if base.get("report_type") == "notice":
        gaps.append(_gap("notice", f"The {period} filing is a notice: holdings are reported by another "
                         "manager, so no table is available for this filer.", True, [base["accession"]]))
        return [], [], gaps
    if base.get("rows") is None:
        gaps.append(_gap("table_missing", f"Table retrieval failed for {base['accession']}: "
                         f"{base.get('validation_detail', '')}", True, [base["accession"]]))
        return [], [], gaps

    amends.sort(key=lambda f: (f.get("amendment_no") or 0, f.get("acceptance_ts") or ""))
    numbers = [f.get("amendment_no") for f in amends]
    if amends and numbers != list(range(1, len(amends) + 1)):
        gaps.append(_gap("amendment_sequence", f"Amendment numbering for {period} is unclear "
                         f"(found {numbers}, expected 1 to {len(amends)}); dependent comparisons are "
                         "suppressed.", True, [f["accession"] for f in amends]))
        return [], [], gaps

    rows, accs = list(base["rows"]), [base["accession"]]
    confidential = base.get("confidential_omitted", False)
    used = [base]
    for a in amends:
        kind = a.get("amendment_type")
        if a.get("rows") is None:
            gaps.append(_gap("amendment_unavailable", f"Amendment {a['accession']} for {period} is "
                             "unavailable; dependent comparisons are suppressed.", True, [a["accession"]]))
            return [], [], gaps
        if kind == "RESTATEMENT":
            rows, accs, used = list(a["rows"]), [a["accession"]], [a]
            confidential = a.get("confidential_omitted", False)
        elif kind == "NEW HOLDINGS":
            rows += a["rows"]
            accs.append(a["accession"])
            used.append(a)
            if "confidential" in (a.get("amendment_reason") or "").lower():
                confidential = False           # evidence: the filing says confidential treatment ended
        else:
            gaps.append(_gap("amendment_type", f"Amendment {a['accession']} has an unknown type "
                             f"({kind}); dependent comparisons are suppressed.", True, [a["accession"]]))
            return [], [], gaps

    bad = [f["accession"] for f in used if f.get("validation") != "VERIFIED"]
    if bad:
        gaps.append(_gap("unverified", f"Filing(s) {', '.join(bad)} for {period} did not reconcile to "
                         "their Summary Page, so they are unverified.", True, bad))
    if confidential:
        gaps.append(_gap("confidential", f"The {period} report omitted confidential positions and no "
                         "later release disclosing them was public yet.", True, accs))
    if base.get("report_type") == "combination":
        gaps.append(_gap("combination", f"The {period} report is a combination report; holdings reported "
                         "by other managers are not included.", True, accs))
    return rows, accs, gaps
