# Development release notes — specialist decisions, batch 1

Production workflow allocates the next unoccupied patch after fresh inventory.
These notes do not preassign a release number.

- Explicit independent core paragraphs can become separate rows; ambiguous
  boundaries preserve the complete description with REVIEW_REQUIRED. Unknown
  geological depths and layer IDs remain blank. Parent metrics repeat visually
  but totals count each unique parent once.
- K uses declared core or explicit linear recovery; boxed values stay separate.
  L uses row-relative K/J formulas, original Word percentages stay in the registry.
  Explicit zero recovery has its own main-sheet row without a fictional layer.
  Drilling without core collection remains in Control. Source conflicts are retained.
- Only proven full duplicates are suppressed, with both IDs, comparison fields,
  source references, retained-row links and effects on totals in the local audit.
- Combined lithology and literal positive oil/hydrocarbon evidence use conservative
  rules; negation does not become saturation. Raw descriptions and numeric marks
  remain available. No inferred geological depths or normalized well identifiers.
- Input template remains core23-v1. Output core24-v1 adds X with the exact
  document well identifier. Reordered semantic columns remain supported.
- Russian Control status/legend, human source locators, conflict links, separate
  record registry and source blocks improve navigation and traceability.
- Every Word candidate receives an explicit outcome. GUI has a named results
  table; CLI JSON includes summary/results. Unique private batch reports include
  the census and full audit. Existing results are skipped with a reason.
- Linux/Windows checks and unpacked EXE acceptance cover the updated contract.
  Immutable source/version/hash binding and the production queue are preserved.

Runtime remains offline without Office, Python installation, LLM, OCR, GPU or
network document APIs. Existing bounded DOC/DOCX readers and fail-closed policy
remain. User geological and visual Excel acceptance is PENDING. Formula caches
are computed by spreadsheet applications, not openpyxl. Private inputs and audits
are excluded from source, public assets and handoff.
