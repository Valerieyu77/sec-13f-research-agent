# Submission

Fill this in and commit it. A submission missing the video link is incomplete.

## Who

- **Name:** Suhang Yu
- **NetID:** pinkiey2

## Video

3 minutes, narrated, showing a cold-start pipeline run and your agent answering
a question.

Upload to Illinois MediaSpace: https://mediaspace.illinois.edu/upload/media
Set visibility to **Unlisted**.

- **Link:** https://mediaspace.illinois.edu/media/t/1_l161arfu

## Chapters attempted

Mark what you completed. Partial work still gets read.

- [x] 1 · Source
- [x] 2 · Interrogate
- [x] 3 · Structure
- [x] 4 · Serve
- [x] 5 · Show
- [ ] Bonus 1 — Notice attribution
- [ ] Bonus 2 — CUSIP validation

## Checklist

- [x] `python check_submission.py` passes
- [x] Repo is **private** and `dsrsBOT` is a collaborator with Read access
- [x] Video uploaded to MediaSpace, visibility **Unlisted**, link tested
- [ ] Repository URL submitted at <https://ikompete.dsrs.illinois.edu/competition/16>
- [x] `python verify.py` passes
- [x] Pipeline run twice; output is byte-identical
- [x] `output/filings.parquet`, `output/holdings.parquet` committed
- [x] `output/filers.csv`, `output/filings/`, `submission/eda.py` committed
- [x] `DEPENDENCIES.md`, `AI_USAGE.md`, and `ASSUMPTIONS.md` filled in
- [x] No API keys, tokens, or credentials committed
- [x] Frozen files unmodified

## Anything we should know

Local development and testing for Chapter 4's agent used a small local model (Ollama,
llama3.2:3b) rather than the grading environment's vLLM + Gemma setup, since the
latter wasn't available to test against locally. This model doesn't support guided
JSON decoding, so intent classification was less reliable in local testing than it
should be at grading time; the query-execution logic itself was validated directly,
bypassing the LLM, against known facts already established in Chapters 2 and 3. Full
details are in submission/ASSUMPTIONS.md.

## Video sharing

We may share your video publicly to show what candidates built. If you would rather we
did not, write "do not share" here:

- **Preference:**