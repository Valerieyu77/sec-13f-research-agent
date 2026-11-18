# SEC 13F Research Agent

**A Python data pipeline and LLM-assisted research agent for exploring institutional investment holdings from SEC EDGAR filings.**

Developed for the 2026 DSRS Data Curator Challenge at the University of Illinois Urbana-Champaign.

## Overview

SEC Form 13F filings disclose quarterly holdings reported by institutional investment managers. Although the filings are publicly available, analyzing them across managers and reporting periods requires collecting, validating, and standardizing data from multiple disclosures.

This project combines two components:

- **Data pipeline:** Retrieves SEC filings, reconciles manager identifiers, parses disclosures, and produces structured Parquet datasets.
- **Research agent:** Interprets natural-language questions and uses structured data queries to investigate institutional holdings.

The goal is to make public financial disclosures easier to explore while keeping the underlying data and assumptions traceable.

## Key Features

### 1. SEC EDGAR Data Collection

- Processes a roster of 20 institutional investment managers.
- Retrieves relevant Form 13F filings for two quarterly reporting periods.
- Validates Central Index Key (CIK) identifiers against SEC records.
- Handles filing selection, data retrieval, and local caching.

### 2. Data Cleaning and Structuring

- Parses SEC filing data into structured records.
- Standardizes holdings information across different filing formats.
- Documents data-quality issues and assumptions.
- Exports filing-level and holdings-level datasets in Apache Parquet format.

### 3. LLM-Assisted Research Agent

- Accepts natural-language questions about institutional holdings.
- Interprets questions and maps them to structured analytical operations.
- Uses Python-based queries to retrieve and compare holdings data.
- Supports traceable answers grounded in the underlying dataset.

## Tech Stack

| Category | Technologies |
|---|---|
| Language | Python |
| Data processing | PyArrow, CSV, XML |
| Data source | SEC EDGAR |
| Storage | Apache Parquet |
| AI integration | LLM-assisted query interpretation |
| Development | Git, Docker |

## Project Structure

```text
main.py                 # SEC data collection and processing pipeline
agents/
  answer.py             # Natural-language research agent
  llm.py                # LLM interface supplied by the challenge
submission/
  ASSUMPTIONS.md        # Data decisions and validation notes
  SUBMISSION.md         # Original competition submission
docs/                   # Challenge documentation
output/                 # Generated structured datasets
tests/                  # Project tests
verify.py               # Dataset verification
```

Some infrastructure and validation files were provided by the challenge organizers. The repository preserves the original competition structure.

## Getting Started

Requires Python 3.12.

```bash
pip install -r requirements.txt -r requirements-extra.txt
cp .env.example .env
```

Configure the model endpoint in `.env`, then run:

```bash
python main.py --user-agent "Your Name email@example.com"
```

To ask a research question:

```bash
python -m agents.answer "Which manager held the most Nvidia in 2026 Q2?"
```

To validate the generated dataset:

```bash
python verify.py
```

The pipeline uses the SEC EDGAR API. A valid identifying User-Agent is required.

## Data Quality Considerations

Institutional filings can contain inconsistencies that affect downstream analysis.

For example, the challenge's initial manager roster included incorrect CIK identifiers. These were checked against SEC records rather than relying only on name similarity.

The project also documents decisions related to filing formats, identifiers, and data integrity.

See [ASSUMPTIONS.md](submission/ASSUMPTIONS.md) for additional details.

## Project Background

This project was completed as part of the **DSRS Data Curator Challenge (2026)**.

The challenge provided the project specification, starter repository, and certain supporting files. My implementation focused on building the data processing pipeline, investigating data-quality issues, and implementing the research agent.

The repository is maintained as a portfolio project demonstrating data engineering, structured financial analysis, and LLM integration.
