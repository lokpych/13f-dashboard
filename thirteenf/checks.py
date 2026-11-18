"""Review flags, coverage handling and the disclosure timeline."""
import numpy as np
import pandas as pd

import config

CLEAN_RATIOS = [2, 3, 4, 5, 6, 8, 10, 15, 20, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10]
SUPPRESSED = "Not comparable (partial coverage)"


def split_flags(df):
    """Flag clean quantity ratios. If the value per share moved the opposite way, the pair looks like a
    corporate action (not a trade), so the directional claim is suspended; otherwise it is only flagged."""
    out = df.copy()
    out["claim_ok"] = True
    flags = out["review_flag"].astype(object).copy()
    for i, r in out.iterrows():
        p, c = r["quantity_prior"], r["quantity_cur"]
        if p <= 0 or c <= 0 or p == c:
            continue
        ratio = c / p
        hit = next((t for t in CLEAN_RATIOS if abs(ratio / t - 1) < 0.001), None)
        if hit is None:
            continue
        px_p, px_c = r["value_usd_prior"] / p, r["value_usd_cur"] / c
        suspected = px_p > 0 and abs((px_c / px_p) * ratio - 1) < 0.25
        note = f"Quantity ratio is exactly {hit:g}x: possible split"
        if suspected:
            note += " (value per share moved inversely); directional claim suspended"
            out.at[i, "claim_ok"] = False
        flags[i] = (flags[i] + "; " if flags[i] else "") + note
    out["review_flag"] = flags
    return out


def apply_coverage(result, pri_gaps, cur_gaps):
    """Suppress claims that depend on missing data when either quarter is partial."""
    df = result.copy()
    notes, partial = [], False
    for g in list(pri_gaps) + list(cur_gaps):
        msg = g["message"] if isinstance(g, dict) else str(g)
        if msg not in notes:
            notes.append(msg)
        partial = partial or (g.get("partial", True) if isinstance(g, dict) else True)
    if partial:
        appear = df["status"].isin(["Newly reported", "No longer reported"])
        df.loc[appear, "status"] = SUPPRESSED
        df.loc[appear, "claim_ok"] = False
        df[["weight_prior", "weight_cur", "weight_change_pp"]] = np.nan
    return df, notes


def disclosure_timeline(filings):
    """One row per amendment: what was filed, when it became public, and whether it revealed history."""
    rows = []
    for f in filings:
        if not f.get("is_amendment"):
            continue
        period = f.get("report_period")
        filed = f.get("filing_date")
        days = None
        if period and filed:
            days = (pd.Timestamp(filed) - pd.Timestamp(period)).days
        late = days is not None and days > config.LATE_DAYS
        rows.append({
            "filed": filed,
            "accepted": f.get("acceptance_ts"),
            "report_period": pd.Timestamp(period).date().isoformat() if period else None,
            "form": f.get("form"),
            "amendment_no": f.get("amendment_no"),
            "amendment_type": f.get("amendment_type"),
            "entries": f.get("parsed_entry_total"),
            "reported_value": f.get("parsed_value_total"),
            "days_after_period_end": days,
            "historical_release": bool(late and f.get("amendment_type") == "NEW HOLDINGS"),
            "accession": f["accession"],
        })
    cols = ["filed", "accepted", "report_period", "form", "amendment_no", "amendment_type", "entries",
            "reported_value", "days_after_period_end", "historical_release", "accession"]
    df = pd.DataFrame(rows, columns=cols)
    return df.sort_values("accepted").reset_index(drop=True) if len(df) else df
