"""Your agent: a question in, a structured answer out.

Architecture (see submission/ASSUMPTIONS.md for the full reasoning):

  1. One guided-JSON call to the LLM translates the natural-language question into a
     structured, bounded "intent" (an action name plus a handful of free-text hints).
     The model never sees any holdings data -- it only ever sees the question text and
     a fixed instruction. This keeps cost low (one call per question) and keeps
     attacker-controlled filing text (issuer names, etc.) out of the model's input
     entirely, since we never put retrieved data back into a prompt.

  2. All actual data access, fuzzy entity matching, and computation happens in plain,
     deterministic Python against the local Parquet files -- read-only, no LLM
     involved. This is what makes answers reproducible and traceable to accessions.

Return this shape. Nothing else on stdout.

    {
      "answer":  <number | string | list | null>,
      "unit":    "USD" | "SHARES" | "COUNT" | "PERCENT" | "NAME" | "DATE" | "NONE",
      "sources": ["0001423053-26-000012", ...]
    }
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from agents.llm import complete, LLMError

OUTPUT = Path(__file__).resolve().parents[1] / "output"
FILINGS = OUTPUT / "filings.parquet"
HOLDINGS = OUTPUT / "holdings.parquet"

VALID_UNITS = {"USD", "SHARES", "COUNT", "PERCENT", "NAME", "DATE", "NONE"}
VALID_QUARTERS = {"2026Q1", "2026Q2"}

ACTIONS = [
    "top_holder_value",       # which fund held the most of an issuer, by value, in a quarter
    "top_change_shares",      # which fund's share count in an issuer grew the most, Q1->Q2
    "distinct_issuer_count",  # how many distinct issuers a fund reported, in a quarter
    "total_value",            # a fund's total reported holdings value, in a quarter
    "list_notice_filers",     # which roster funds filed 13F-NT (vs 13F-HR) in a quarter
    "position_value",         # a fund's reported value in a specific issuer, in a quarter
    "top_putcall_count",      # which fund reported the most option positions of a given type
    "top_position_for_fund",  # a fund's single largest position (issuer + value), in a quarter
    "position_growth",        # for an issuer held in both quarters, did the position grow/shrink
    "unsupported",            # anything the dataset cannot answer -- always available
]

INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ACTIONS},
        "fund_hint": {"type": ["string", "null"], "description": "manager name mentioned, if any"},
        "issuer_hint": {"type": ["string", "null"], "description": "company/security mentioned, if any"},
        "quarter_hint": {"type": ["string", "null"], "description": "quarter mentioned, verbatim, e.g. '2026 Q2' or '2026 Q3'"},
        "putcall_hint": {"type": ["string", "null"], "description": "'call' or 'put' if options are mentioned"},
    },
    "required": ["action", "fund_hint", "issuer_hint", "quarter_hint", "putcall_hint"],
}

INTENT_SYSTEM_PROMPT = """\
You classify a research question about SEC 13F filings into a structured intent.
You do not have access to any filing data -- only classify the question itself.
Pick the single best-fitting `action` from the allowed list. If nothing fits, or the
question asks about a time period other than 2026 Q1/Q2, use "unsupported".
Extract hints verbatim from the question; do not guess or normalize company names.
If a hint is not mentioned, use null.
Respond with ONLY a single JSON object with exactly these keys: action, fund_hint,
issuer_hint, quarter_hint, putcall_hint. No other text.
"""


def _load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    filings = pd.read_parquet(FILINGS)
    holdings = pd.read_parquet(HOLDINGS)
    return filings, holdings


def _normalize_quarter(raw: str | None) -> str | None:
    """Map free-text like '2026 Q2', 'Q2 2026', '2026Q2' to '2026Q2'; else None."""
    if not raw:
        return None
    compact = raw.upper().replace(" ", "").replace("-", "")
    for q in VALID_QUARTERS:
        if q in compact:
            return q
    return None


def _resolve_fund(hint: str | None, filings: pd.DataFrame) -> str | None:
    """Fuzzy-match a free-text fund hint against the 20-name roster. No data leaves
    this process into the model -- matching happens entirely in Python."""
    if not hint:
        return None
    names = filings["fund_name"].unique().tolist()
    matches = difflib.get_close_matches(hint, names, n=1, cutoff=0.5)
    if matches:
        return matches[0]
    hint_lower = hint.lower()
    for name in names:
        if hint_lower in name.lower() or name.lower() in hint_lower:
            return name
    return None


def _resolve_issuer_cusips(hint: str | None, holdings: pd.DataFrame) -> list[str]:
    """Fuzzy-match a free-text issuer hint against distinct issuer names, then return
    every CUSIP associated with the best-matching name(s). Matching on CUSIP rather
    than free-text name handles the same issuer being spelled differently by
    different filers."""
    if not hint:
        return []
    distinct_names = holdings["name_of_issuer"].dropna().unique().tolist()
    matches = difflib.get_close_matches(hint, distinct_names, n=5, cutoff=0.5)
    if not matches:
        hint_lower = hint.lower()
        matches = [n for n in distinct_names if hint_lower in n.lower()][:5]
    if not matches:
        return []
    cusips = holdings.loc[holdings["name_of_issuer"].isin(matches), "cusip"].unique().tolist()
    return cusips


def _null_result() -> dict[str, Any]:
    return {"answer": None, "unit": "NONE", "sources": []}

def _execute(intent: dict[str, Any], filings: pd.DataFrame, holdings: pd.DataFrame) -> dict[str, Any]:
    def _clean(v):
        if v is None:
            return None
        s = str(v).strip()
        return None if s.lower() in ("", "null", "none", "n/a") else s

    action = intent.get("action")
    quarter = _normalize_quarter(_clean(intent.get("quarter_hint")))
    fund = _resolve_fund(_clean(intent.get("fund_hint")), filings)
    cusips = _resolve_issuer_cusips(_clean(intent.get("issuer_hint")), holdings)
    putcall = (_clean(intent.get("putcall_hint")) or "").lower() or None

    if intent.get("quarter_hint") and quarter is None and action != "list_notice_filers":
        print(f"[answer] quarter '{intent.get('quarter_hint')}' is out of scope", file=sys.stderr)
        return _null_result()

    if action == "unsupported":
        print("[answer] classified as unsupported by intent model", file=sys.stderr)
        return _null_result()

    if action == "top_holder_value":
        if not cusips or not quarter:
            print("[answer] top_holder_value: missing issuer or quarter", file=sys.stderr)
            return _null_result()
        rows = holdings[(holdings["cusip"].isin(cusips)) & (holdings["report_quarter"] == quarter)]
        if rows.empty:
            return _null_result()
        by_cik = rows.groupby("cik")["value"].sum()
        top_cik = by_cik.idxmax()
        top_value = int(by_cik.loc[top_cik])
        fund_name = filings.loc[filings["cik"] == top_cik, "fund_name"].iloc[0]
        sources = rows.loc[rows["cik"] == top_cik, "accession_number"].unique().tolist()
        return {"answer": [fund_name, top_value], "unit": "USD", "sources": sources}

    if action == "top_change_shares":
        if not cusips:
            print("[answer] top_change_shares: missing issuer", file=sys.stderr)
            return _null_result()
        rows = holdings[holdings["cusip"].isin(cusips)]
        by_fund_q = rows.groupby(["cik", "report_quarter"])["ssh_prnamt"].sum().unstack(fill_value=0)
        if "2026Q1" not in by_fund_q.columns or "2026Q2" not in by_fund_q.columns:
            return _null_result()
        delta = by_fund_q["2026Q2"] - by_fund_q["2026Q1"]
        if delta.empty:
            return _null_result()
        top_cik = delta.idxmax()
        top_delta = int(delta.loc[top_cik])
        fund_name = filings.loc[filings["cik"] == top_cik, "fund_name"].iloc[0]
        sources = rows.loc[rows["cik"] == top_cik, "accession_number"].unique().tolist()
        return {"answer": [fund_name, top_delta], "unit": "SHARES", "sources": sources}

    if action == "distinct_issuer_count":
        if not fund or not quarter:
            print("[answer] distinct_issuer_count: missing fund or quarter", file=sys.stderr)
            return _null_result()
        cik = filings.loc[filings["fund_name"] == fund, "cik"].iloc[0]
        rows = holdings[(holdings["cik"] == cik) & (holdings["report_quarter"] == quarter)]
        if rows.empty:
            return _null_result()
        count = rows["cusip"].nunique()
        sources = rows["accession_number"].unique().tolist()
        return {"answer": int(count), "unit": "COUNT", "sources": sources}

    if action == "total_value":
        if not fund or not quarter:
            print("[answer] total_value: missing fund or quarter", file=sys.stderr)
            return _null_result()
        cik = filings.loc[filings["fund_name"] == fund, "cik"].iloc[0]
        rows = holdings[(holdings["cik"] == cik) & (holdings["report_quarter"] == quarter)]
        if rows.empty:
            return _null_result()
        total = int(rows["value"].sum())
        sources = rows["accession_number"].unique().tolist()
        return {"answer": total, "unit": "USD", "sources": sources}

    if action == "list_notice_filers":
        if not quarter:
            print("[answer] list_notice_filers: missing quarter", file=sys.stderr)
            return _null_result()
        rows = filings[(filings["report_quarter"] == quarter) & (filings["report_type"] == "13F NOTICE")]
        if rows.empty:
            return {"answer": [], "unit": "NAME", "sources": []}
        return {
            "answer": rows["fund_name"].tolist(),
            "unit": "NAME",
            "sources": rows["accession_number"].tolist(),
        }

    if action == "position_value":
        if not fund or not cusips or not quarter:
            print("[answer] position_value: missing fund, issuer, or quarter", file=sys.stderr)
            return _null_result()
        cik = filings.loc[filings["fund_name"] == fund, "cik"].iloc[0]
        rows = holdings[
            (holdings["cik"] == cik)
            & (holdings["cusip"].isin(cusips))
            & (holdings["report_quarter"] == quarter)
        ]
        if rows.empty:
            return {"answer": 0, "unit": "USD", "sources": []}
        total = int(rows["value"].sum())
        sources = rows["accession_number"].unique().tolist()
        return {"answer": total, "unit": "USD", "sources": sources}

    if action == "top_putcall_count":
        if not quarter or not putcall:
            print("[answer] top_putcall_count: missing quarter or put/call type", file=sys.stderr)
            return _null_result()
        rows = holdings[
            (holdings["report_quarter"] == quarter)
            & (holdings["put_call"].str.lower() == putcall)
        ]
        if rows.empty:
            return _null_result()
        by_cik = rows.groupby("cik").size()
        top_cik = by_cik.idxmax()
        top_count = int(by_cik.loc[top_cik])
        fund_name = filings.loc[filings["cik"] == top_cik, "fund_name"].iloc[0]
        sources = rows.loc[rows["cik"] == top_cik, "accession_number"].unique().tolist()
        return {"answer": [fund_name, top_count], "unit": "COUNT", "sources": sources}

    if action == "top_position_for_fund":
        if not fund or not quarter:
            print("[answer] top_position_for_fund: missing fund or quarter", file=sys.stderr)
            return _null_result()
        cik = filings.loc[filings["fund_name"] == fund, "cik"].iloc[0]
        rows = holdings[(holdings["cik"] == cik) & (holdings["report_quarter"] == quarter)]
        if rows.empty:
            return _null_result()
        top_row = rows.loc[rows["value"].idxmax()]
        return {
            "answer": [top_row["name_of_issuer"], int(top_row["value"])],
            "unit": "USD",
            "sources": [top_row["accession_number"]],
        }

    if action == "position_growth":
        if not cusips:
            print("[answer] position_growth: missing issuer", file=sys.stderr)
            return _null_result()
        rows = holdings[holdings["cusip"].isin(cusips)]
        by_fund_q = rows.groupby(["cik", "report_quarter"])["value"].sum().unstack(fill_value=None)
        if "2026Q1" not in by_fund_q.columns or "2026Q2" not in by_fund_q.columns:
            return _null_result()
        both = by_fund_q.dropna(subset=["2026Q1", "2026Q2"])
        if both.empty:
            return _null_result()
        both = both.assign(delta=both["2026Q2"] - both["2026Q1"])
        top_cik = both["delta"].abs().idxmax()
        direction = "grew" if both.loc[top_cik, "delta"] > 0 else "shrank"
        fund_name = filings.loc[filings["cik"] == top_cik, "fund_name"].iloc[0]
        sources = rows.loc[rows["cik"] == top_cik, "accession_number"].unique().tolist()
        return {"answer": [fund_name, direction], "unit": "NAME", "sources": sources}

    print(f"[answer] unhandled action: {action}", file=sys.stderr)
    return _null_result()


def main(question: str) -> dict[str, Any]:
    """Answer `question` against the dataset. Do not rename or change the signature."""
    filings, holdings = _load_data()

    try:
        raw = complete(
            [
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            schema=INTENT_SCHEMA,
            max_tokens=256,
        )
        # vLLM's guided decoding (grading environment) never wraps output in markdown
        # fences. Some local dev servers (e.g. Ollama, which has no guided_json
        # support) sometimes do -- strip that defensively before parsing.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        intent = json.loads(cleaned)

        # Guard against a model returning an action string outside our enum (expected
        # under vLLM's guided decoding, which structurally cannot happen -- but a
        # defensive check costs nothing and matters when testing against servers
        # without guided-JSON support, like local Ollama).
        if intent.get("action") not in ACTIONS:
            close = difflib.get_close_matches(str(intent.get("action")), ACTIONS, n=1, cutoff=0.2)
            intent["action"] = close[0] if close else "unsupported"
    except Exception as exc:
        print(f"[answer] intent classification failed: {exc}", file=sys.stderr)
        return _null_result()

    print(f"[answer] intent: {intent}", file=sys.stderr)

    try:
        return _execute(intent, filings, holdings)
    except Exception as exc:
        print(f"[answer] execution error: {exc}", file=sys.stderr)
        return _null_result()


def _cli() -> int:
    if len(sys.argv) < 2:
        print('usage: python -m agents.answer "your question"', file=sys.stderr)
        return 2

    result = main(sys.argv[1])

    if not isinstance(result, dict):
        print(f"main() must return a dict, got {type(result).__name__}", file=sys.stderr)
        return 1
    missing = {"answer", "unit", "sources"} - set(result)
    if missing:
        print(f"result missing key(s): {sorted(missing)}", file=sys.stderr)
        return 1
    if result["unit"] not in VALID_UNITS:
        print(f"unit must be one of {sorted(VALID_UNITS)}, got {result['unit']!r}",
              file=sys.stderr)
        return 1
    if not isinstance(result["sources"], list):
        print("sources must be a list of accession numbers", file=sys.stderr)
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())