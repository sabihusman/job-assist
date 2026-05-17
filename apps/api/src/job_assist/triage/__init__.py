"""Triage pipeline — three layers, this PR ships layer 1.

  1. Hard rules (this module)  — cheap deterministic filters; no LLM, no embedding.
  2. Embedding similarity      — vector search against operator profile (Week 3).
  3. LLM verdict               — top-N postings only (Week 3-4).

The hard-rule layer is intentionally pure-functional and stateless; the
caller (eventually a daily triage cron) fetches the rows it needs and
hands them in.
"""
