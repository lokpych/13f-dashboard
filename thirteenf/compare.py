"""Normalize, group and compare positions across two quarters."""
import numpy as np
import pandas as pd

import config

KEYS = ["cusip", "class_norm", "put_call", "qty_type"]


def multiplier(filing_date):
    """Filings before 2023-01-03 report value in $ thousands; later filings use whole dollars."""
    if not filing_date:
        raise ValueError("filing_date is needed to normalize reported values")
    return 1000 if str(filing_date)[:10] < config.UNIT_CHANGE_DATE else 1


def norm_class(text):
    t = " ".join((text or "").upper().replace(".", "").replace(",", "").split())
    return config.CLASS_EQUIVALENTS.get(t, t)


def normalize(records, filing_date):
    df = pd.DataFrame(records)
    mult = multiplier(filing_date)
    out = pd.DataFrame({
        "accession": df["accession"],
        "row_ordinal": df["row_ordinal"],
        "issuer": df["issuer"],
        "cusip": df["cusip"].astype(str).str.upper().str.strip(),
        "class_norm": df["class_title"].map(norm_class),
        "put_call": df["put_call"].fillna("").astype(str).str.upper(),
        "qty_type": df["qty_type"].fillna("SH").astype(str).str.upper(),
        "quantity": df["quantity"].astype("int64"),
        "value_raw": df["value_raw"].astype("int64"),
    })
    out["value_usd"] = out["value_raw"] * mult
    per_unit = out["value_usd"] / out["quantity"].replace(0, np.nan)
    shares = out["qty_type"] == "SH"
    out["unit_flag"] = shares & ((per_unit > 1_000_000) | (per_unit < 0.01))
    return out


def group_positions(df):
    """One row per security identity; keeps the contributing (accession, row) pairs."""
    rows = []
    for key, g in df.groupby(KEYS, sort=False):
        rows.append({
            **dict(zip(KEYS, key)),
            "issuer": g["issuer"].iloc[0],
            "quantity": int(g["quantity"].sum()),
            "value_usd": int(g["value_usd"].sum()),
            "n_rows": len(g),
            "unit_flag": bool(g["unit_flag"].any()),
            "sources": list(zip(g["accession"], g["row_ordinal"])),
        })
    return pd.DataFrame(rows, columns=KEYS + ["issuer", "quantity", "value_usd", "n_rows",
                                              "unit_flag", "sources"])


def _status(prior, cur, value_change):
    if prior == 0 and cur > 0:
        return "Newly reported"
    if cur == 0 and prior > 0:
        return "No longer reported"
    if cur > prior:
        return "Increased"
    if cur < prior:
        return "Reduced"
    return "Unchanged"


def compare(q_prior, q_cur):
    a = q_prior.drop(columns=["sources", "n_rows"]).rename(
        columns={"quantity": "quantity_prior", "value_usd": "value_usd_prior",
                 "issuer": "issuer_prior", "unit_flag": "unit_prior"})
    b = q_cur.drop(columns=["sources", "n_rows"]).rename(
        columns={"quantity": "quantity_cur", "value_usd": "value_usd_cur",
                 "issuer": "issuer_cur", "unit_flag": "unit_cur"})
    m = a.merge(b, on=KEYS, how="outer")
    m["issuer"] = m["issuer_cur"].fillna(m["issuer_prior"])
    for c in ["quantity_prior", "quantity_cur", "value_usd_prior", "value_usd_cur"]:
        m[c] = m[c].fillna(0).astype("int64")
    m["unit_flag"] = m["unit_prior"].fillna(False).astype(bool) | m["unit_cur"].fillna(False).astype(bool)

    m["qty_change"] = m["quantity_cur"] - m["quantity_prior"]
    m["qty_pct"] = np.where(m["quantity_prior"] > 0, m["qty_change"] / m["quantity_prior"].replace(0, np.nan), np.nan)
    m["value_change"] = m["value_usd_cur"] - m["value_usd_prior"]
    tot_p, tot_c = m["value_usd_prior"].sum(), m["value_usd_cur"].sum()
    m["weight_prior"] = m["value_usd_prior"] / tot_p if tot_p else np.nan
    m["weight_cur"] = m["value_usd_cur"] / tot_c if tot_c else np.nan
    m["weight_change_pp"] = (m["weight_cur"] - m["weight_prior"]) * 100
    m["status"] = [_status(p, c, v) for p, c, v in zip(m["quantity_prior"], m["quantity_cur"], m["value_change"])]
    m["review_flag"] = np.where(m["unit_flag"], "Check units: implausible value per share", "")

    cols = ["issuer", "cusip", "class_norm", "put_call", "qty_type", "status", "quantity_prior",
            "quantity_cur", "qty_change", "qty_pct", "value_usd_prior", "value_usd_cur", "value_change",
            "weight_prior", "weight_cur", "weight_change_pp", "review_flag"]
    return m[cols].sort_values(["issuer", "cusip", "put_call"]).reset_index(drop=True)
