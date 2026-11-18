"""Read one 13F filing (cover page, summary page, information table) and validate it."""
from datetime import datetime

from lxml import etree

import config

NS_BASE = "https://www.sec.gov/Archives/edgar/data"


def _iso(mmddyyyy):
    try:
        return datetime.strptime(mmddyyyy.strip(), "%m-%d-%Y").date().isoformat()
    except (ValueError, AttributeError):
        return None


def _first(root, name):
    got = root.xpath(f"//*[local-name()='{name}']")
    return (got[0].text or "").strip() if got else ""


def _child(el, name):
    got = el.xpath(f"*[local-name()='{name}']")
    return (got[0].text or "").strip() if got else ""


def _int(text):
    text = (text or "").replace(",", "").strip()
    if not text:
        return 0
    return int(float(text))


def accession_dir(cik, accession):
    return f"{NS_BASE}/{int(cik)}/{accession.replace('-', '')}"


def parse_cover(xml_bytes):
    root = etree.fromstring(xml_bytes)
    rtype = _first(root, "reportType").upper()
    kind = "notice" if "NOTICE" in rtype else "combination" if "COMBINATION" in rtype else "holdings"
    refs = []
    for el in root.xpath("//*[local-name()='otherManager2']/*[local-name()='otherManager']"):
        refs.append({"role": "included in table", "name": _child(el, "name"),
                     "file_number": _child(el, "form13FFileNumber")})
    for el in root.xpath("//*[local-name()='coverPage']//*[local-name()='otherManager']"):
        refs.append({"role": "reports on behalf of filer", "name": _child(el, "name"),
                     "file_number": _child(el, "form13FFileNumber")})
    entry = _first(root, "tableEntryTotal")
    value = _first(root, "tableValueTotal")
    return {
        "form": _first(root, "submissionType"),
        "report_period": _iso(_first(root, "periodOfReport") or _first(root, "reportCalendarOrQuarter")),
        "report_type": kind,
        "is_amendment": _first(root, "isAmendment").lower() == "true",
        "amendment_no": int(_first(root, "amendmentNo")) if _first(root, "amendmentNo").isdigit() else None,
        "amendment_type": _first(root, "amendmentType").upper() or None,
        "amendment_reason": _first(root, "reasonForNonConfidentiality") or None,
        "confidential_omitted": _first(root, "isConfidentialOmitted").lower() == "true",
        "manager_refs": refs,
        "summary_entry_total": int(entry) if entry.isdigit() else None,
        "summary_value_total": int(value) if value.isdigit() else None,
    }


def parse_table(xml_bytes, accession):
    root = etree.fromstring(xml_bytes)
    rows = []
    for i, el in enumerate(root.xpath("//*[local-name()='infoTable']"), start=1):
        amt = el.xpath("*[local-name()='shrsOrPrnAmt']")
        amt = amt[0] if amt else el
        vote = el.xpath("*[local-name()='votingAuthority']")
        vote = vote[0] if vote else el
        rows.append({
            "accession": accession,
            "row_ordinal": i,
            "issuer": _child(el, "nameOfIssuer"),
            "class_title": _child(el, "titleOfClass"),
            "cusip": _child(el, "cusip").upper(),
            "figi": _child(el, "figi"),
            "value_raw": _int(_child(el, "value")),
            "quantity": _int(_child(amt, "sshPrnamt")),
            "qty_type": _child(amt, "sshPrnamtType").upper() or "SH",
            "put_call": _child(el, "putCall").upper(),
            "discretion": _child(el, "investmentDiscretion"),
            "other_managers": _child(el, "otherManager"),
            "vote_sole": _int(_child(vote, "Sole")),
            "vote_shared": _int(_child(vote, "Shared")),
            "vote_none": _int(_child(vote, "None")),
        })
    return rows


def _is_info_table(data):
    try:
        return etree.fromstring(data).tag.endswith("informationTable")
    except etree.XMLSyntaxError:
        return False


def _load_filing_full(client, cik, accession):
    """Fetch and parse one accession. 'rows' is missing when the table could not be retrieved."""
    base = accession_dir(cik, accession)
    out = {"accession": accession, "cik": str(cik).zfill(10), "links": {"folder": base + "/"},
           "rows": None}
    try:
        listing, meta = client.get_json(base + "/index.json")
        names = [i["name"] for i in listing["directory"]["item"]]
    except Exception as e:                       # index unavailable
        out.update(validation="INDEX_UNAVAILABLE", validation_detail=str(e),
                   summary_entry_total=None, parsed_entry_total=0,
                   summary_value_total=None, parsed_value_total=0)
        del out["rows"]
        return out
    out["retrieved_at"] = meta["retrieved_at"]
    xmls = [n for n in names if n.lower().endswith(".xml")]
    if "primary_doc.xml" not in xmls:
        out.update(validation="UNSUPPORTED_FORMAT",
                   validation_detail="No XML cover page (older text-format filing).",
                   summary_entry_total=None, parsed_entry_total=0,
                   summary_value_total=None, parsed_value_total=0)
        del out["rows"]
        return out
    cover = parse_cover(client.get(base + "/primary_doc.xml")[0])
    out.update(cover)
    out["links"]["primary_doc"] = base + "/primary_doc.xml"

    if cover["report_type"] == "notice":
        out.update(rows=[], parsed_entry_total=0, parsed_value_total=0, validation="NOTICE_NO_TABLE",
                   validation_detail="Notice report: holdings are reported by another manager.")
        return out

    table = None
    for name in xmls:
        if name == "primary_doc.xml":
            continue
        data = client.get(base + "/" + name)[0]
        if _is_info_table(data):
            table, out["links"]["information_table"] = data, base + "/" + name
            break
    if table is None:
        out.update(validation="TABLE_MISSING", parsed_entry_total=0, parsed_value_total=0,
                   validation_detail="Cover page found but the information table could not be retrieved.")
        del out["rows"]
        return out

    rows = parse_table(table, accession)
    out["rows"] = rows
    out["parsed_entry_total"] = len(rows)
    out["parsed_value_total"] = sum(r["value_raw"] for r in rows)
    if cover["summary_entry_total"] is None or cover["summary_value_total"] is None:
        out.update(validation="UNVERIFIED_NO_SUMMARY", validation_detail="Summary Page totals missing.")
    elif (out["parsed_entry_total"], out["parsed_value_total"]) == (cover["summary_entry_total"],
                                                                    cover["summary_value_total"]):
        out.update(validation="VERIFIED", validation_detail="Entry count and value total match Summary Page.")
    else:
        out.update(validation="MISMATCH",
                   validation_detail=f"Summary says {cover['summary_entry_total']} entries / "
                                     f"{cover['summary_value_total']}, parsed {out['parsed_entry_total']} / "
                                     f"{out['parsed_value_total']}.")
    return out


def load_filing(client, cik, accession):
    """Like _load_filing_full, but keeps only the rows config.ROW_SUBSETS names for this filer (if any)."""
    out = _load_filing_full(client, cik, accession)
    rule = config.ROW_SUBSETS.get(str(cik).zfill(10))
    if rule and out.get("rows"):
        out["rows_full_count"] = len(out["rows"])
        out["rows"] = [r for r in out["rows"] if (r.get("other_managers") or "").strip() == rule["tag"]]
        out["subset_label"] = rule["label"]
    return out
