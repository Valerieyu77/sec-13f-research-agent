# AI Usage

## Tools used

Claude (Anthropic), used throughout as a coding assistant. And Local Ollama (llama3.2:3b) was used only as a development-time LLM endpoint for testing
Chapter 4's agent — it did not generate any code.

## Where you used them

| Chapter | How you used AI |
|---|---|
| 1 · Source | Used Claude to draft the EDGAR-fetching code, but every CIK correction was independently verified by me against SEC's own EDGAR company search (`cgi-bin/browse-edgar`) before being accepted — including catching a case (The Baupost Group LLC) where the "obvious" fuzzy-matched correction was actually wrong, because the closest name match pointed to an entity that stopped filing in 2002. |
| 2 · Interrogate | Used Claude to draft `submission/eda.py`. Every finding reported there was confirmed by inspecting the actual output — e.g. the CUSIP duplication finding was checked by pulling up the 13 raw rows sharing one CUSIP in AQR's filing and confirming by hand that each had a different reported value, ruling out a parsing bug before accepting it as a real finding. |
| 3 · Structure | Used Claude to draft the XML parsing code, after I inspected the raw cover-page and information-table XML across several filers myself to identify the actual field names and the three different namespace styles in use. |
| 4 · Serve | Used Claude to design the two-step architecture (LLM classifies intent -> deterministic Python executes it) and draft `agents/answer.py`. Since the grading model (vLLM + guided JSON) was not available locally, I validated the query-execution logic myself by hand-constructing intents for several example questions and checking the results against known facts already established in Chapter 2/3 (e.g. Renaissance Technologies' Q2 distinct-issuer count matched the cover page's own declared total exactly). |

## What you would change

Given more time, I would test the intent classification against a larger local model
to get a more realistic read on its accuracy before submission, since the 3B model
available to me locally clearly understates what the graded model should manage. I
would also want to stress-test the fuzzy fund/issuer name matching against more edge
cases than the handful I checked by hand.