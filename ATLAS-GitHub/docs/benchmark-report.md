# ATLAS Tool vs No-Tool Benchmark Report

Date: 29 July 2026

## Purpose

This benchmark compares ATLAS tool-assisted workflows against direct LLM-only workflows.

The goal is to measure:

- Accuracy
- Capability difference
- Latency
- Input reduction
- Reliability

---

# Task 1 — Dependency Graph Analysis

## Tool Path (ATLAS)

Command:
Dependency graph analysis of project files.

Results:

- Files scanned: 40
- Internal dependency edges: 49
- Most depended file:
  - tools.py → 13 dependents
  - storage.py → 12 dependents
- Circular imports detected: 0

Latency:

~4.4 seconds

Status:

Correct analysis.

Note:

The API returned success:false because the router incorrectly passed:

focus_file:""

The underlying dependency scan result is valid.

---

## No-Tool Path

Direct LLM analysis without dependency graph tool.

Results:

- Latency: ~30 seconds
- Generated architecture analysis

Issues:

- Incorrect architecture assumptions
- Claimed possible circular imports
- Did not match actual repository structure

Conclusion:

The tool produced a verifiable repository-level analysis.

---

# Task 2 — YouTube Video Summarization

## Tool Path (ATLAS)

Input:

YouTube URL transcript extraction.

Results:

- Transcript extracted: 22,128 characters
- Summary generated successfully
- Grounded in actual video transcript

Latency:

75.67 seconds

---

## No-Tool Path

Direct LLM request.

Results:

- Latency: 2.97 seconds
- Model correctly stated it could not access external video content.

Conclusion:

Tool provided a capability unavailable to the base model.

---

# Task 3 — Document Summarization

Document:

IMPLEMENTATION_STATE_FULL.md

Document size:

140,782 characters

---

# Run 1 Results

## ATLAS Tool Path

Input handling:

- Prompt characters sent: 40,181
- Document cap applied: YES
- Original document: 140,782 chars

Reduction:

Approximately 71.5% fewer characters sent.

Performance:

- Total duration: 61.63 seconds

---

## Raw LLM Path

Input:

Entire document pasted directly.

Results:

- Prompt characters sent: 140,837
- Total duration: 52.37 seconds

Observation:

Raw path required the entire document context.

---

# Important Finding

Token counters showed identical prompt_eval_count values:

Tool:

2050 tokens

Raw:

2050 tokens

Therefore:

This benchmark should NOT claim token reduction.

The measurable improvement is:

- Document payload reduction
- Controlled context handling
- Protection against oversized prompts

---

# Overall Findings

## Capability

ATLAS tools enable:

✅ Repository inspection  
✅ External content extraction  
✅ Controlled document processing  

---

## Reliability

Tool outputs are:

- Grounded in actual data
- Reproducible
- Inspectable

---

## Main Advantage

The biggest difference is not only speed.

The advantage is giving the LLM access to capabilities it does not naturally have:

- File analysis
- Code dependency inspection
- External data extraction
- Context management

---

# Raw Evidence Files

The July report named the following files. The verification search did not recover them. See the [November benchmark audit](verification/BENCHMARK_VERIFICATION.md).

- atlas_tools_depgraph_v2.json
- atlas_tools_doc_run1.json
- atlas_notools_doc_run1.json
- tokens_tool_run1.json
- tokens_raw_run1.json

