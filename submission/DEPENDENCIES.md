# Dependencies

| Library | Version | Why |
|---|---|---|
| pandas | 3.0.5 | Used in `agents/answer.py` for grouping, filtering, and aggregating `filings.parquet`/`holdings.parquet` (e.g. summing `value` by manager, computing quarter-over-quarter deltas). `pyarrow` alone (already in `requirements.txt`) is sufficient for reading/writing Parquet but far more verbose for this kind of tabular aggregation; pandas is the standard tool for it and keeps the agent's query logic readable. |

## Anything you considered and rejected

Considered using `pyarrow.compute` directly for the aggregations in `agents/answer.py`
to avoid adding a dependency, since `pyarrow` is already required. Rejected it because
the multi-step groupby/unstack logic needed for quarter-over-quarter comparisons
(Chapter 4's "who added the most Nvidia shares" style questions) is substantially more
verbose and harder to get right in raw `pyarrow.compute` than in pandas, and this is
exactly the kind of question a researcher using this tool would actually ask.