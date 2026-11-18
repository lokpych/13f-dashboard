"""Typed claims built in code. The brief is rendered from these claims only."""
from dataclasses import dataclass, field

import pandas as pd

import config

KEYS = ["cusip", "class_norm", "put_call", "qty_type"]
VALUE_ONLY_MIN_MOVE = 0.05      # unchanged quantity needs a 5% value move to be worth a claim
LIMITS = "Quarter-end long positions as reported; not trade dates; shorts excluded."


@dataclass
class Claim:
    id: str
    kind: str                     # quantity | value_only | disclosure | coverage
    filer: str
    scope: str
    periods: tuple
    metric: str = ""
    unit: str = ""
    security: str = ""
    cusip: str = ""
    instrument: str = ""
    direction: str = ""
    prior: float = None
    cur: float = None
    change: float = None
    pct: float = float("nan")
    value_change: float = None
    limits: str = LIMITS
    accessions: list = field(default_factory=list)
    rows: dict = field(default_factory=dict)      # accession -> contributing row numbers
    priority: float = 0.0
    required: bool = False
    text: str = ""


def link(accession, cik):
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{accession}-index.htm")


def _sources_map(df):
    return {tuple(r[k] for k in KEYS): r["sources"] for _, r in df.iterrows()}


def _rows_by_accession(sources):
    out = {}
    for acc, ordinal in sources:
        out.setdefault(acc, []).append(int(ordinal))
    return {a: sorted(v) for a, v in out.items()}


def build_claims(clean, q_prior, q_cur, *, filer, scope, periods, acc_prior, acc_cur,
                 coverage_notes, timeline):
    prior_src, cur_src = _sources_map(q_prior), _sources_map(q_cur)
    claims, n = [], 0

    def new_id():
        nonlocal n
        n += 1
        return f"C{n:03d}"

    for _, r in clean.iterrows():
        if not r["claim_ok"]:
            continue
        key = tuple(r[k] for k in KEYS)
        if r["status"] in ("Increased", "Reduced", "Newly reported", "No longer reported"):
            kind, direction, priority = "quantity", r["status"], abs(r["value_change"])
        elif (r["status"] == "Unchanged" and r["value_usd_prior"]
              and abs(r["value_change"]) / r["value_usd_prior"] >= VALUE_ONLY_MIN_MOVE):
            # quantity unchanged but the reported value moved materially: ranked below quantity changes
            kind, direction, priority = "value_only", "Unchanged quantity, value changed", abs(r["value_change"]) * 0.001
        else:
            continue
        rows = {}
        for src in (prior_src.get(key, []), cur_src.get(key, [])):
            for acc, nums in _rows_by_accession(src).items():
                rows[acc] = sorted(set(rows.get(acc, []) + nums))
        accs = [a for a in (acc_prior, acc_cur) if a in rows] or list(rows)
        accs += [a for a in rows if a not in accs]
        instrument = r["qty_type"] + (f" {r['put_call']}" if r["put_call"] else "")
        claims.append(Claim(
            id=new_id(), kind=kind, filer=filer, scope=scope, periods=tuple(periods),
            metric="reported quantity" if kind == "quantity" else "reported value",
            unit="shares" if r["qty_type"] == "SH" else "principal amount",
            security=r["issuer"], cusip=r["cusip"], instrument=instrument, direction=direction,
            prior=int(r["quantity_prior"]), cur=int(r["quantity_cur"]), change=int(r["qty_change"]),
            pct=r["qty_pct"], value_change=int(r["value_change"]),
            accessions=accs, rows=rows, priority=float(priority)))

    for _, t in timeline.iterrows():
        if not t["historical_release"]:
            continue
        claims.append(Claim(
            id=new_id(), kind="disclosure", filer=filer, scope=scope, periods=(t["report_period"],),
            metric="disclosure event", direction="Historical release",
            accessions=[t["accession"]], priority=1e12,
            text=(f"{t['form']} accepted {str(t['accepted'])[:10]} revealed {int(t['entries'])} previously "
                  f"unreported position(s) for the {t['report_period']} quarter "
                  f"(reported value ${int(t['reported_value']):,})")))

    for note in coverage_notes:
        claims.append(Claim(id=new_id(), kind="coverage", filer=filer, scope=scope, periods=tuple(periods),
                            metric="coverage limit", direction="Coverage note",
                            accessions=[a for a in (acc_prior, acc_cur) if a], priority=1e15,
                            required=True, text=note))
    return claims


def render(claim, cik):
    links = ", ".join(f"[{a}]({link(a, cik)})" for a in claim.accessions)
    if claim.kind in ("coverage", "disclosure"):
        return f"{claim.text} (sources: {links})"
    if claim.kind == "value_only":
        return (f"{claim.security} ({claim.cusip}, {claim.instrument}): quantity unchanged at "
                f"{claim.cur:,} {claim.unit}, reported value changed by ${claim.value_change:+,}, "
                f"{claim.periods[0]} to {claim.periods[1]} (sources: {links})")
    pct = f" ({claim.pct:+.1%})" if pd.notna(claim.pct) else ""
    return (f"{claim.security} ({claim.cusip}, {claim.instrument}): {claim.direction}, "
            f"{claim.prior:,} to {claim.cur:,} {claim.unit}{pct}, {claim.periods[0]} to "
            f"{claim.periods[1]} (sources: {links})")


def validate_selection(selected_ids, claims, max_bullets=None):
    """Reject unknown claim IDs and selections that leave out a required coverage note."""
    known = {c.id for c in claims}
    unknown = [i for i in selected_ids if i not in known]
    missing = [c.id for c in claims if c.required and c.id not in selected_ids]
    return (not unknown and not missing), {"unknown": unknown, "missing_required": missing}


def brief(claims, cik, selected_ids=None, max_bullets=config.MAX_BULLETS):
    """Up to five bullets. Required coverage notes always come first; the rest are the largest movers."""
    if selected_ids is None:
        required = [c for c in claims if c.required]
        rest = sorted((c for c in claims if not c.required), key=lambda c: -c.priority)
        chosen = required + rest[:max(0, max_bullets - len(required))]
        chosen.sort(key=lambda c: (not c.required, c.id))
    else:
        ok, problems = validate_selection(selected_ids, claims)
        if not ok:
            raise ValueError(f"Brief selection rejected: {problems}")
        by_id = {c.id: c for c in claims}
        chosen = [by_id[i] for i in selected_ids][:max_bullets]
    return [render(c, cik) for c in chosen], [c.id for c in chosen]


def trace(claims, claim_id):
    """Accession -> row numbers that support a claim."""
    return next(c.rows for c in claims if c.id == claim_id)
