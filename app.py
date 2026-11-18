import io, re
import pandas as pd
import streamlit as st
from lxml import etree

from thirteenf.sec_client import SECClient
from thirteenf import parser, store, compare as cmp, amendments as amd
from thirteenf import checks as chk, claims as clm, export as exp

st.set_page_config(page_title="13F Investor Dashboard", layout="wide")
st.title("SEC Form 13F Investor Dashboard")
st.caption("Quarter-end long positions as reported to the SEC. Not trade dates, "
           "excludes shorts, may omit small or confidential holdings. No recommendations.")

DEMO = {"Berkshire Hathaway": "0001067983",
        "Bridgewater Associates": "0001350694",
        "Coatue Management": "0001135730",
        "Fortress Investment Group": "0001380393",
        "ExodusPoint Capital Management": "0001736225",
        "Pershing Square Inc. (Pershing Square Capital Mgmt)": "0002026053",
        "Soros Fund Management (Quantum)": "0001029160",
        "SkyBridge Capital II (partial)": "0001639668",
        "Renaissance Technologies": "0001037389",
        "PIMCO (via Allianz, PIMCO rows only)": "0001535323"}


@st.cache_resource
def get_client():
    return SECClient()


@st.cache_data(ttl=3600)
def search_filers(name):
    url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
           f"&company={name}&type=13F-HR&dateb=&owner=include&count=40&output=atom")
    data, _ = get_client().get(url, refresh=True)
    root = etree.fromstring(data)
    out = []
    for e in root.xpath("//*[local-name()='entry']"):
        name = e.xpath("string(.//*[local-name()='company-info']/*[local-name()='name'])").strip()
        cik = e.xpath("string(.//*[local-name()='company-info']/*[local-name()='cik'])").strip()
        if name and cik.isdigit():
            out.append({"name": name, "cik": cik.zfill(10)})
    return pd.DataFrame(out).drop_duplicates("cik") if out else pd.DataFrame()


def pretty(df):
    """Screen-friendly version of the comparison table (export keeps raw numbers)."""
    return pd.DataFrame({
        "Security": df["issuer"].str.title(),
        "CUSIP": df["cusip"],
        "Class": df["class_norm"],
        "Type": df["qty_type"] + df["put_call"].apply(lambda x: f" {x}" if x else ""),
        "Status": df["status"],
        "Prior shares": df["quantity_prior"],
        "Current shares": df["quantity_cur"],
        "Share change": df["qty_change"],
        "% change": df["qty_pct"] * 100,
        "Prior value ($M)": df["value_usd_prior"] / 1e6,
        "Current value ($M)": df["value_usd_cur"] / 1e6,
        "Prior % of 13F value": df["weight_prior"] * 100,
        "Current % of 13F value": df["weight_cur"] * 100,
        "Weight change (pp)": df["weight_change_pp"],
        "Flag": df["review_flag"],
    })


TABLE_FORMAT = {
    "Prior shares": st.column_config.NumberColumn(format="localized"),
    "Current shares": st.column_config.NumberColumn(format="localized"),
    "Share change": st.column_config.NumberColumn(format="localized"),
    "% change": st.column_config.NumberColumn(format="%+.1f%%"),
    "Prior value ($M)": st.column_config.NumberColumn(format="dollar"),
    "Current value ($M)": st.column_config.NumberColumn(format="dollar"),
    "Prior % of 13F value": st.column_config.NumberColumn(format="%.2f%%"),
    "Current % of 13F value": st.column_config.NumberColumn(format="%.2f%%"),
    "Weight change (pp)": st.column_config.NumberColumn(format="%+.2f"),
}

# ---------- 1. Onboarding ----------
st.sidebar.header("1. Choose filer")
mode = st.sidebar.radio("Find by", ["Demo manager", "Name search", "CIK"])
cik = None
if mode == "Demo manager":
    cik = DEMO[st.sidebar.selectbox("Manager", list(DEMO))]
elif mode == "CIK":
    raw = st.sidebar.text_input("CIK (digits)")
    cik = raw.strip().zfill(10) if raw.strip().isdigit() else None
else:
    q = st.sidebar.text_input("Fund or manager name")
    if q:
        cands = search_filers(q)
        if cands.empty:
            st.warning("Unresolved: no 13F filer matches that name. No holdings are attributed.")
        else:
            pick = st.sidebar.selectbox("Select the reporting filer",
                                        [f"{r.name} | CIK {r.cik}" for r in cands.itertuples()])
            cik = pick.split("CIK ")[1]
            st.info(f"Requested name: **{q}**. A name match alone does not prove the fund's "
                    "holdings are isolated, so this is shown as a **manager aggregate**.")

if not cik:
    st.stop()

client = get_client()
subs, meta = client.submissions(cik)
hist = amd.thirteenf_filings(subs, client=client)
st.subheader(f"{subs['name']}  ·  CIK {cik}")
st.caption(f"Scope: manager aggregate · Filing list retrieved {meta['retrieved_at']}")

periods = sorted(hist["reportDate"].dropna().unique(), reverse=True)
if len(periods) == 0:
    st.error("No 13F filings found for this CIK.")
    st.stop()

# ---------- 2. Periods and cutoff ----------
st.sidebar.header("2. Periods")
cur_p = st.sidebar.selectbox("Current report period", periods)
prior_p = (pd.Timestamp(cur_p) - pd.offsets.QuarterEnd(1)).date().isoformat()
st.sidebar.write(f"Prior period: **{prior_p}**")
cutoff = None
if st.sidebar.checkbox("Public-as-of view"):
    cutoff = st.sidebar.date_input("Accepted on or before").isoformat() + "T23:59:59"


@st.cache_data(show_spinner="Loading filings from EDGAR...")
def load_period(cik, period):
    con = store.connect()
    out = []
    for _, r in hist[hist["reportDate"] == period].iterrows():
        pf = parser.load_filing(client, cik, r["accessionNumber"])
        pf.update(form=r["form"], filing_date=r["filingDate"],
                  acceptance_ts=r["acceptanceDateTime"])
        if "rows" in pf:
            store.save_filing(con, pf)
        out.append(pf)
    return out


cur_f, prior_f = load_period(cik, cur_p), load_period(cik, prior_p)
cur_rows, cur_acc, cur_gaps = amd.effective_disclosure(cur_f, cutoff) if cur_f else ([], [], [])
pri_rows, pri_acc, pri_gaps = amd.effective_disclosure(prior_f, cutoff) if prior_f else ([], [], [])

c1, c2, c3 = st.columns(3)
c1.metric("Current quarter end", cur_p)
c2.metric("Prior quarter end", prior_p)
c3.metric("Public as of", cutoff[:10] if cutoff else "Latest")

# ---------- 3. Comparison ----------
if not cur_rows or not pri_rows:
    missing = "current" if not cur_rows else "prior"
    st.error(f"Comparison unavailable: the {missing} quarter has no public holdings table "
             "for this filer and cutoff. A missing filing is never treated as zero.")
    for g in list(cur_gaps) + list(pri_gaps):
        st.warning(g["message"])
    st.stop()


def to_positions(rows, filings):
    dates = {f["accession"]: f["filing_date"] for f in filings}
    df = pd.DataFrame(rows)
    parts = [cmp.normalize(g.to_dict("records"), dates[a]) for a, g in df.groupby("accession")]
    return cmp.group_positions(pd.concat(parts))


q_prior, q_cur = to_positions(pri_rows, prior_f), to_positions(cur_rows, cur_f)
result = chk.split_flags(cmp.compare(q_prior, q_cur))
clean, notes = chk.apply_coverage(result, pri_gaps, cur_gaps)
for n in notes:
    st.warning(n)

timeline = chk.disclosure_timeline(prior_f + cur_f)
claims = clm.build_claims(clean, q_prior, q_cur, filer=subs["name"], scope="manager aggregate",
                          periods=(prior_p, cur_p), acc_prior=pri_acc[-1], acc_cur=cur_acc[-1],
                          coverage_notes=notes, timeline=timeline)
bullets, ids = clm.brief(claims, cik)

st.markdown("### Brief")
for b in bullets:
    st.markdown(f"- {b}")

st.markdown("### Holdings comparison")
status = st.multiselect("Filter by status", sorted(clean["status"].unique()))
view = clean[clean["status"].isin(status)] if status else clean
st.dataframe(pretty(view), use_container_width=True, hide_index=True, height=600,
             column_config=TABLE_FORMAT)

st.markdown("### Disclosure events")
if len(timeline):
    st.dataframe(timeline, use_container_width=True, hide_index=True)
else:
    st.write("No amendments for these periods.")

st.markdown("### Sources")
for f in prior_f + cur_f:
    st.markdown(f"- {f.get('form')} · period {f['report_period']} · filed "
                f"{f.get('filing_date')} · {f['validation']} · "
                f"[{f['accession']}]({exp.link(f['accession'], cik)})")

# ---------- 4. Excel export ----------
used = [f for f in prior_f + cur_f if f["accession"] in pri_acc + cur_acc]
buf = io.BytesIO()
exp.export(buf, filer=subs["name"], cik=cik, scope="manager aggregate",
           periods=(prior_p, cur_p), cutoff=cutoff, holdings=view, filings=used,
           bullets=bullets, coverage_notes=notes,
           filters=", ".join(status) if status else "none")
st.download_button("Download Excel report", buf.getvalue(),
                   file_name=f"13F_{cik}_{prior_p}_vs_{cur_p}.xlsx")
