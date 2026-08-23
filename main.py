#!/usr/bin/env python3
"""Run the whole pipeline: fetch from EDGAR, parse, write the dataset.

    python main.py --user-agent "FirstName LastName netid@illinois.edu"

This is how we run your submission, so it must work from a clean checkout with nothing
in output/. Everything below is yours to rewrite — add modules, packages, classes,
whatever fits. Only two things are fixed:

  - this file is the entry point, and it accepts --user-agent
  - it writes output/filings.parquet and output/holdings.parquet

Start with docs/01-source.md. Check your output with `python verify.py`.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import httpx
from datetime import date
from xml.etree import ElementTree as ET
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
FILERS = ROOT / "filers.csv"
OUTPUT = ROOT / "output"
CACHE = ROOT / ".cache"

# Scope. See docs/01-source.md -- filter report periods on reportDate, and exclude
# anything accepted after the cutoff.
REPORT_PERIODS = ("2026-03-31", "2026-06-30")
FILING_DATE_CUTOFF = "2026-08-18"  # inclusive
FORMS_IN_SCOPE = {"13F-HR", "13F-HR/A", "13F-NT", "13F-NT/A"}

CIK_LOOKUP_URL = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"

# SEC rejects requests without a contact address, and a run that gets the department
# blocked is worth failing fast on. Loose on purpose: we check an address is present,
# not that it is well-formed.
UA_PATTERN = re.compile(r"^\S.*\s+[^@\s]+@[^@\s]+\.[a-z]{2,}\s*$", re.I)

# Manually verified via SEC's EDGAR company search (browse-edgar) and cik-lookup-data.txt,
# because automated name normalization either found nothing or matched a stale/incorrect
# entity. See submission/ASSUMPTIONS.md for the reasoning behind each one.
#
#   Tudor Investment Corp: given CIK 854157 belongs to a different entity. The manager
#     that actually files 13F as "Tudor Investment Corp" is CIK 923093.
#   Situational Awareness LP: given CIK 1697748 does not resolve to any registrant.
#     EDGAR company search finds "Situational Awareness LP", CIK 2045724 (distinct
#     from the similarly-named "Situational Awareness Partners LP", CIK 2038540).
#   The Baupost Group LLC: given CIK 1061768 initially looked wrong because
#     cik-lookup-data.txt's closest name match, CIK 1054420 ("Baupost Group LLC /ADV"),
#     stopped filing 13Fs in 2002. The given CIK 1061768 ("Baupost Group LLC/MA") is
#     the entity that is currently active, so it was left as `given` -- not corrected.
CIK_CORRECTIONS: dict[str, str] = {
    "Tudor Investment Corp": "923093",
    "Situational Awareness LP": "2045724",
}


def load_filers() -> list[dict[str, str]]:
    """The roster the researcher supplied. At least one CIK in here is wrong."""
    with FILERS.open(newline="") as fh:
        return list(csv.DictReader(fh))


def verify_and_write_filers(user_agent: str, output: Path) -> list[dict[str, str]]:
    """Chapter 1.1: reconcile filers.csv CIKs, write output/filers.csv.

    Returns the corrected roster as a list of {fund_name, cik, cik_source} dicts.
    """
    filers = load_filers()

    rows = []
    for row in filers:
        name = row["fund_name"]
        given_cik = row["cik"].lstrip("0") or "0"

        if name in CIK_CORRECTIONS:
            cik = CIK_CORRECTIONS[name]
            source = "corrected"
        else:
            cik = given_cik
            source = "given"

        rows.append({"fund_name": name, "cik": cik, "cik_source": source})

    rows.sort(key=lambda r: int(r["cik"]))

    output.mkdir(parents=True, exist_ok=True)
    out_path = output / "filers.csv"
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["fund_name", "cik", "cik_source"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[source] wrote {len(rows)} rows to {out_path}")
    return rows


def fetch_submissions(cik: str, user_agent: str, client: httpx.Client) -> dict:
    padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{padded}.json"
    resp = client.get(url, headers={"User-Agent": user_agent}, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def find_matching_filings(data: dict, fund_name: str, cik: str) -> list[dict]:
    """Chapter 1.2: filter a manager's filings to the two in-scope 13F quarters."""
    recent = data["filings"]["recent"]
    matches = []
    n = len(recent["accessionNumber"])
    for i in range(n):
        form = recent["form"][i]
        report_date = recent["reportDate"][i]
        filing_date = recent["filingDate"][i]
        if (
            form in FORMS_IN_SCOPE
            and report_date in REPORT_PERIODS
            and filing_date <= FILING_DATE_CUTOFF
        ):
            matches.append({
                "fund_name": fund_name,
                "cik": cik,
                "accession_number": recent["accessionNumber"][i],
                "form_type": form,
                "report_date": report_date,
                "filing_date": filing_date,
            })
    return matches


def _index_json_url(cik: str, accession_nodash: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/index.json"


def _doc_url(cik: str, accession_nodash: str, filename: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{filename}"


def _find_info_table_filename(index_data: dict) -> str | None:
    """The information table is whichever XML in the filing isn't primary_doc.xml."""
    items = index_data["directory"]["item"]
    xml_files = [item["name"] for item in items if item["name"].lower().endswith(".xml")]
    candidates = [f for f in xml_files if f.lower() != "primary_doc.xml"]
    if len(candidates) == 1:
        return candidates[0]
    return None


def download_filing(
    cik: str, accession: str, form_type: str, user_agent: str, output: Path, client: httpx.Client
) -> tuple[str, Path | None]:
    """Chapter 1.3/1.4: download a filing's cover page and information table (when one
    exists), caching each to disk so re-runs skip work already done.

    We cache the cover page (primary_doc.xml) in addition to the information table
    because filings.parquet needs cover-page-only fields (filing_manager, crd_number,
    table_entry_total, etc.) that Chapter 3 requires -- see submission/ASSUMPTIONS.md.
    """
    accession_nodash = accession.replace("-", "")
    filings_dir = output / "filings" / cik
    filings_dir.mkdir(parents=True, exist_ok=True)

    info_path = filings_dir / f"{accession_nodash}.xml"
    cover_path = filings_dir / f"{accession_nodash}_cover.xml"

    need_index = not (info_path.exists() and cover_path.exists())
    index_data = None
    if need_index:
        idx_resp = client.get(
            _index_json_url(cik, accession_nodash),
            headers={"User-Agent": user_agent},
            timeout=10.0,
        )
        idx_resp.raise_for_status()
        index_data = idx_resp.json()
        time.sleep(0.12)

    statuses = []

    # Cover page (always present)
    if cover_path.exists():
        statuses.append("cover=cached")
    else:
        doc_resp = client.get(
            _doc_url(cik, accession_nodash, "primary_doc.xml"),
            headers={"User-Agent": user_agent},
            timeout=10.0,
        )
        doc_resp.raise_for_status()
        time.sleep(0.12)
        cover_path.write_bytes(doc_resp.content)
        statuses.append("cover=fetched")

    # Information table (absent for notices)
    if info_path.exists():
        statuses.append("info_table=cached")
    else:
        filename = _find_info_table_filename(index_data) if index_data else None
        if filename is None:
            statuses.append("info_table=none (notice)")
        else:
            doc_resp = client.get(
                _doc_url(cik, accession_nodash, filename),
                headers={"User-Agent": user_agent},
                timeout=10.0,
            )
            doc_resp.raise_for_status()
            time.sleep(0.12)
            info_path.write_bytes(doc_resp.content)
            statuses.append("info_table=fetched")

    return " ".join(statuses), info_path

def local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _find_text(root, name: str) -> str | None:
    """First descendant element whose local (namespace-stripped) tag matches `name`."""
    for el in root.iter():
        if local_name(el.tag) == name:
            return el.text
    return None


def parse_cover(cover_path: Path, form_type: str, report_date: str, filing_date: str) -> dict:
    """Chapter 3: extract a filings.parquet row from a cover page (primary_doc.xml)."""
    tree = ET.parse(cover_path)
    root = tree.getroot()

    report_period = date.fromisoformat(report_date)
    report_quarter = f"{report_period.year}Q{(report_period.month - 1) // 3 + 1}"

    filing_manager = _find_text(root, "name")  # filingManager/name
    report_type = _find_text(root, "reportType")
    form_13f_file_number = _find_text(root, "form13FFileNumber")
    crd_number = _find_text(root, "crdNumber")
    sec_file_number = _find_text(root, "secFileNumber")

    other_included_managers_count = _find_text(root, "otherIncludedManagersCount")
    table_entry_total = _find_text(root, "tableEntryTotal")
    table_value_total = _find_text(root, "tableValueTotal")

    amendment_no = _find_text(root, "amendmentNo")
    amendment_type = _find_text(root, "amendmentType")

    return {
        "cik": None,  # filled in by caller
        "fund_name": None,  # filled in by caller
        "filing_manager": filing_manager,
        "form_type": form_type,
        "report_period": report_period,
        "report_quarter": report_quarter,
        "filing_date": date.fromisoformat(filing_date),
        "is_amendment": form_type.endswith("/A"),
        "amendment_no": int(amendment_no) if amendment_no else None,
        "amendment_type": amendment_type,
        "report_type": report_type,
        "form_13f_file_number": form_13f_file_number,
        "crd_number": crd_number,
        "sec_file_number": sec_file_number,
        "other_included_managers_count": (
            int(other_included_managers_count) if other_included_managers_count is not None else None
        ),
        "table_entry_total": int(table_entry_total) if table_entry_total is not None else None,
        "table_value_total": int(table_value_total) if table_value_total is not None else None,
    }


def parse_holdings(info_path: Path, cik: str, report_quarter: str) -> list[dict]:
    """Chapter 3: extract holdings.parquet rows from an information table.

    Matches child elements by namespace-stripped local name, since filers use
    inconsistent namespace prefixes (none, ns1:, n1:) for the identical schema.
    """
    tree = ET.parse(info_path)
    root = tree.getroot()

    rows = []
    for entry in root.iter():
        if local_name(entry.tag) != "infoTable":
            continue

        fields: dict[str, str] = {}
        ssh_prnamt = None
        ssh_prnamt_type = None
        voting_sole = voting_shared = voting_none = None

        for child in entry:
            name = local_name(child.tag)
            if name == "shrsOrPrnAmt":
                for gc in child:
                    gname = local_name(gc.tag)
                    if gname == "sshPrnamt":
                        ssh_prnamt = gc.text
                    elif gname == "sshPrnamtType":
                        ssh_prnamt_type = gc.text
            elif name == "votingAuthority":
                for gc in child:
                    gname = local_name(gc.tag)
                    if gname == "Sole":
                        voting_sole = gc.text
                    elif gname == "Shared":
                        voting_shared = gc.text
                    elif gname == "None":
                        voting_none = gc.text
            else:
                fields[name] = child.text

        rows.append({
            "cik": cik,
            "report_quarter": report_quarter,
            "name_of_issuer": fields.get("nameOfIssuer"),
            "title_of_class": fields.get("titleOfClass"),
            "cusip": fields.get("cusip"),
            "figi": fields.get("figi"),
            "value": int(fields["value"]),
            "ssh_prnamt": int(ssh_prnamt),
            "ssh_prnamt_type": ssh_prnamt_type,
            "put_call": fields.get("putCall"),
            "investment_discretion": fields.get("investmentDiscretion"),
            "other_manager": fields.get("otherManager"),
            "voting_sole": int(voting_sole),
            "voting_shared": int(voting_shared),
            "voting_none": int(voting_none),
        })
    return rows


def run(user_agent: str, output: Path) -> None:
    """Build the dataset: verify CIKs, find/download filings, parse into Parquet."""
    filers = verify_and_write_filers(user_agent, output)

    all_matches: list[dict] = []
    with httpx.Client() as client:
        for row in filers:
            cik = row["cik"]
            name = row["fund_name"]
            data = fetch_submissions(cik, user_agent, client)
            matches = find_matching_filings(data, name, cik)
            # deterministic order: earliest report_date first
            matches.sort(key=lambda m: m["report_date"])
            all_matches.extend(matches)
            time.sleep(0.12)

        print(f"[source] found {len(all_matches)} in-scope filings (expected 40)")

        for m in all_matches:
            status, path = download_filing(m["cik"], m["accession_number"], m["form_type"], user_agent, output, client)
            print(f"[source] {m['fund_name']:<40} {m['report_date']}  {status:<30} {path}")

    # ---- Chapter 3: parse into filings.parquet / holdings.parquet ----------------
    filings_rows = []
    holdings_rows = []

    for m in all_matches:
        cik = m["cik"].zfill(10)
        accession_nodash = m["accession_number"].replace("-", "")
        cik_dir = output / "filings" / m["cik"]
        cover_path = cik_dir / f"{accession_nodash}_cover.xml"
        info_path = cik_dir / f"{accession_nodash}.xml"

        cover_row = parse_cover(cover_path, m["form_type"], m["report_date"], m["filing_date"])
        cover_row["accession_number"] = m["accession_number"]
        cover_row["cik"] = cik
        cover_row["fund_name"] = m["fund_name"]
        filings_rows.append(cover_row)

        if info_path.exists():
            for h in parse_holdings(info_path, cik, cover_row["report_quarter"]):
                h["accession_number"] = m["accession_number"]
                holdings_rows.append(h)

    filings_schema = pa.schema([
        ("accession_number", pa.string()),
        ("cik", pa.string()),
        ("fund_name", pa.string()),
        ("filing_manager", pa.string()),
        ("form_type", pa.string()),
        ("report_period", pa.date32()),
        ("report_quarter", pa.string()),
        ("filing_date", pa.date32()),
        ("is_amendment", pa.bool_()),
        ("amendment_no", pa.int32()),
        ("amendment_type", pa.string()),
        ("report_type", pa.string()),
        ("form_13f_file_number", pa.string()),
        ("crd_number", pa.string()),
        ("sec_file_number", pa.string()),
        ("other_included_managers_count", pa.int32()),
        ("table_entry_total", pa.int64()),
        ("table_value_total", pa.int64()),
    ])
    holdings_schema = pa.schema([
        ("accession_number", pa.string()),
        ("cik", pa.string()),
        ("report_quarter", pa.string()),
        ("name_of_issuer", pa.string()),
        ("title_of_class", pa.string()),
        ("cusip", pa.string()),
        ("figi", pa.string()),
        ("value", pa.int64()),
        ("ssh_prnamt", pa.int64()),
        ("ssh_prnamt_type", pa.string()),
        ("put_call", pa.string()),
        ("investment_discretion", pa.string()),
        ("other_manager", pa.string()),
        ("voting_sole", pa.int64()),
        ("voting_shared", pa.int64()),
        ("voting_none", pa.int64()),
    ])

    filings_cols = {name: [r.get(name) for r in filings_rows] for name in filings_schema.names}
    holdings_cols = {name: [r.get(name) for r in holdings_rows] for name in holdings_schema.names}

    filings_table = pa.table(filings_cols, schema=filings_schema)
    holdings_table = pa.table(holdings_cols, schema=holdings_schema)

    pq.write_table(filings_table, output / "filings.parquet", compression="snappy")
    pq.write_table(holdings_table, output / "holdings.parquet", compression="snappy")

    print(f"[structure] wrote {len(filings_rows)} rows to output/filings.parquet (expected 40)")
    print(f"[structure] wrote {len(holdings_rows)} rows to output/holdings.parquet")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-agent", required=True,
                    help='required by SEC: "FirstName LastName netid@illinois.edu"')
    ap.add_argument("--output", type=Path, default=OUTPUT)
    args = ap.parse_args()

    if not UA_PATTERN.match(args.user_agent):
        sys.exit(
            "invalid --user-agent.\n"
            "SEC requires a contact address and rejects requests without one.\n"
            '  python main.py --user-agent "Jane Doe jdoe@illinois.edu"'
        )

    args.output.mkdir(parents=True, exist_ok=True)
    run(args.user_agent, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())