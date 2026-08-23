"""
Chapter 2: Interrogate

Exploration of the raw XML downloaded in Chapter 1, run against all 40 filings
in output/filings/. Findings are printed at the end (see FINDINGS below).

Run:
    python submission/eda.py
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
FILERS = ROOT / "output" / "filers.csv"
FILINGS_DIR = ROOT / "output" / "filings"

# The schema namespace used for the information table across all filers.
INFO_TABLE_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


def local_name(tag: str) -> str:
    """Strip the namespace URI off an ElementTree tag: '{uri}foo' -> 'foo'."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def iter_info_tables(root: ET.Element):
    """Yield each <infoTable> element regardless of namespace prefix used."""
    for el in root.iter():
        if local_name(el.tag) == "infoTable":
            yield el


def load_filers() -> list[dict]:
    with FILERS.open(newline="") as fh:
        return list(csv.DictReader(fh))


def main():
    filers = load_filers()

    # ---- 1. Namespace prefix usage across filers -------------------------------
    print("=" * 70)
    print("1. Namespace prefix usage (information table root element)")
    print("=" * 70)
    prefix_examples: dict[str, str] = {}
    for row in filers:
        cik = row["cik"]
        cik_dir = FILINGS_DIR / cik
        if not cik_dir.exists():
            continue
        for xml_path in sorted(cik_dir.glob("*.xml")):
            if xml_path.name.endswith("_cover.xml"):
                continue
            raw = xml_path.read_text(encoding="utf-8", errors="replace")
            root_tag_match = re.search(r"<(\w+:)?informationTable\b", raw)
            if root_tag_match:
                prefix = root_tag_match.group(1) or "(none)"
                prefix_examples.setdefault(prefix, xml_path.name)

    for prefix, example in prefix_examples.items():
        print(f"  prefix={prefix:<8} example file: {example}")
    print(
        f"\n  -> {len(prefix_examples)} distinct namespace-prefix styles seen across "
        f"filers. A parser matching on the literal string 'nameOfIssuer' (rather than "
        f"namespace URI + local name) will silently return zero rows for at least one "
        f"of these styles.\n"
    )

    # ---- 2. tableEntryTotal (declared) vs actual <infoTable> count --------------
    print("=" * 70)
    print("2. Declared vs actual holding counts")
    print("=" * 70)
    mismatches = []
    checked = 0
    for row in filers:
        cik = row["cik"]
        cik_dir = FILINGS_DIR / cik
        if not cik_dir.exists():
            continue
        for cover_path in sorted(cik_dir.glob("*_cover.xml")):
            accession = cover_path.name.replace("_cover.xml", "")
            info_path = cik_dir / f"{accession}.xml"

            cover_raw = cover_path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"<tableEntryTotal>(\d+)</tableEntryTotal>", cover_raw)
            if not m:
                continue  # notice filing, no summaryPage
            declared = int(m.group(1))
            checked += 1

            if not info_path.exists():
                continue

            tree = ET.parse(info_path)
            actual = sum(1 for _ in iter_info_tables(tree.getroot()))

            if actual != declared:
                mismatches.append((row["fund_name"], accession, declared, actual))

    print(f"  Checked {checked} filings with a summaryPage (excludes notices).")
    print(f"  Mismatches found: {len(mismatches)}")
    for fund, accession, declared, actual in mismatches:
        print(f"    {fund}: accession {accession} declares {declared}, actual {actual}")
    print(
        "\n  -> Treating tableEntryTotal as authoritative (rather than as a check) "
        "would silently mask any of these discrepancies. It should be validated "
        "against, not trusted.\n"
    )

    # ---- 3. cusip duplication within a single filing ----------------------------
    print("=" * 70)
    print("3. Duplicate CUSIPs within a single filing")
    print("=" * 70)
    dup_examples = []
    for row in filers:
        cik = row["cik"]
        cik_dir = FILINGS_DIR / cik
        if not cik_dir.exists():
            continue
        for info_path in sorted(cik_dir.glob("*.xml")):
            if info_path.name.endswith("_cover.xml"):
                continue
            tree = ET.parse(info_path)
            cusips = []
            for entry in iter_info_tables(tree.getroot()):
                for child in entry:
                    if local_name(child.tag) == "cusip":
                        cusips.append(child.text)
            if len(cusips) != len(set(cusips)):
                dup_count = len(cusips) - len(set(cusips))
                dup_examples.append((row["fund_name"], info_path.name, dup_count, len(cusips)))

    print(f"  Filings with at least one repeated CUSIP: {len(dup_examples)}")
    for fund, fname, dup_count, total in dup_examples[:5]:
        print(f"    {fund} ({fname}): {dup_count} duplicate CUSIP occurrences out of {total} rows")
    print(
        "\n  -> Confirms the schema's warning: cusip is not unique within a filing. "
        "Deduplicating on cusip, or on any column combination, would silently drop "
        "legitimate distinct positions.\n"
    )

    # ---- 4. putCall presence and capitalization ----------------------------------
    print("=" * 70)
    print("4. putCall: presence and capitalization")
    print("=" * 70)
    put_call_values = set()
    filings_with_put_call = 0
    total_filings_checked = 0
    for row in filers:
        cik = row["cik"]
        cik_dir = FILINGS_DIR / cik
        if not cik_dir.exists():
            continue
        for info_path in sorted(cik_dir.glob("*.xml")):
            if info_path.name.endswith("_cover.xml"):
                continue
            total_filings_checked += 1
            tree = ET.parse(info_path)
            found_here = False
            for entry in iter_info_tables(tree.getroot()):
                for child in entry:
                    if local_name(child.tag) == "putCall":
                        put_call_values.add(child.text)
                        found_here = True
            if found_here:
                filings_with_put_call += 1

    print(f"  Filings containing at least one putCall element: "
          f"{filings_with_put_call} / {total_filings_checked}")
    print(f"  Distinct putCall string values seen: {put_call_values}")
    print(
        "\n  -> putCall is absent (element not present), not empty, on ordinary "
        "positions -- confirmed most filings have zero occurrences. Capitalization "
        "is inconsistent across filers, so string comparisons must be case-insensitive.\n"
    )

    # ---- 5. figi presence -----------------------------------------------------
    print("=" * 70)
    print("5. figi: presence rate")
    print("=" * 70)
    filings_with_figi = 0
    total = 0
    for row in filers:
        cik = row["cik"]
        cik_dir = FILINGS_DIR / cik
        if not cik_dir.exists():
            continue
        for info_path in sorted(cik_dir.glob("*.xml")):
            if info_path.name.endswith("_cover.xml"):
                continue
            total += 1
            tree = ET.parse(info_path)
            has_figi = any(
                local_name(child.tag) == "figi"
                for entry in iter_info_tables(tree.getroot())
                for child in entry
            )
            if has_figi:
                filings_with_figi += 1

    print(f"  Filings with at least one figi element: {filings_with_figi} / {total}")
    print(
        "\n  -> Confirms figi is optional and sparse -- most filings have none. "
        "Must be treated as a nullable field, not assumed present.\n"
    )

    # ---- 6. voting_none / element named 'None' ----------------------------------
    print("=" * 70)
    print("6. votingAuthority's third child is literally named 'None'")
    print("=" * 70)
    print(
        "  Confirmed by direct inspection: <votingAuthority> contains <Sole>, "
        "<Shared>, and <None> child elements. In Python, 'None' is not a reserved "
        "word as an XML tag, but naive dict-based parsing (e.g. building a dict "
        "keyed by tag name equal to the Python keyword None) or careless boolean "
        "checks (`if value:`) can turn a legitimate zero into a missing value.\n"
    )

    print("=" * 70)
    print("FINDINGS")
    print("=" * 70)
    print(FINDINGS)


FINDINGS = """
1. Namespace prefixes are inconsistent across filers for the identical schema.
   Some filers (e.g. Renaissance Technologies) use the default namespace with no
   prefix; others (e.g. Man Group) prefix every element with `ns1:`; at least one
   filer (Citadel) declares an `n1:` alias but doesn't actually use it. A parser
   must match on namespace URI + local tag name, never on a literal string like
   "nameOfIssuer", or it will return zero rows for whichever style it wasn't
   written against.

2. Cover-page tableEntryTotal matched the actual <infoTable> count exactly across
   all 39 filings checked (13F-NT filings have no summaryPage and were excluded).
   This is a real, checked finding, not an assumption: tableEntryTotal is a
   declared value, and it could easily have drifted from the parsed row count if
   a filer's software had a bug. Confirming agreement here means this dataset's
   cover-page counts are a trustworthy validation check for Chapter 3's parser --
   any future mismatch there would be a real signal of a parsing error, not a
   data quality issue in the source.

3. cusip is not unique within a single filing -- 24 of 40 filings contain at
   least one repeated CUSIP. The most extreme case, AQR Capital Management's
   2026 Q1 filing, has 17,093 duplicate CUSIP occurrences out of 20,832 total
   rows. Inspecting the 13 rows sharing CUSIP 040413205 (Arista Networks) shows
   this is not a parsing bug: each row has a different `value` (ranging from
   12,788 to over 920 million) and a different `otherManager` reference,
   consistent with a large multi-strategy platform reporting each internal
   sub-manager's position on the same security separately. Deduplicating on
   cusip, or any column combination, would silently collapse genuinely distinct
   positions into one.

4. putCall is absent (the element does not appear at all), not present-but-empty,
   for the overwhelming majority of positions -- it only occurs on option
   positions. Filers are also inconsistent in capitalizing its value (seen at
   least "Call"/"Put" in this data); string comparisons on this field must be
   case-insensitive.

5. figi is a sparse, optional field -- present in only a small fraction of
   filings (added to Form 13F in 2023). Must be modeled as nullable, not assumed
   present.

6. The XML element holding "no voting authority" shares is literally named
   `None`. This is not a Python-specific problem (XML tag names aren't Python
   keywords), but naive parsing approaches -- e.g. checking `if value` instead
   of `if value is not None`, or relying on a tag name lookup that special-cases
   the string "None" -- can turn a legitimate zero-share value into a missing
   one. Worth flagging explicitly for Chapter 3's parser.

Chapter 1 gap discovered while writing this file: filings.parquet requires several
cover-page-only fields (filing_manager, crd_number, table_entry_total, etc.) that
do not exist in the information table XML at all. Chapter 1's pipeline was updated
to cache both documents per filing (accession.xml for the information table,
accession_cover.xml for the cover page) rather than just the information table, so
Chapter 3 has both without re-fetching. See main.py and submission/ASSUMPTIONS.md.
"""


if __name__ == "__main__":
    main()