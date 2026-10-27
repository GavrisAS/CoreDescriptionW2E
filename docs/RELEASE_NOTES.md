# Development release notes

Production workflow allocates the next unoccupied patch after fresh GitHub
inventory. These notes do not preassign a release number.

- Added CORE_INTERVAL: one original core record per Excel row, complete W,
  explicit sampling interval/J/K, separate core IDs and no-core intervals on
  Control. Geological layer/GIS depths and layer numbers are left blank.
- Added visible REVIEW_REQUIRED workbooks in a separate output/review folder.
  Source conflicts, duplicate candidates, original percentages and totals are
  preserved. This is controlled structural transfer, not accepted geological
  interpretation. A closed conflict policy cannot bypass incomplete reading.
- Boxed core has priority over declared core for K; every source metric remains
  separately attributed. J/K/L repeat per layered run, with row-relative formulas.
- DOC empty textbox stories are supported only with full CP-range/PLCF proof.
  Nonempty textboxes remain unsupported; no Office/LibreOffice runtime dependency.
- Physical table provenance separates ancillary side-column labels from W and
  retains descriptions continuing in later rightmost cells. Raw text is retained.
- Long W is retained without truncation. Conservative wrapping reaches Excel's
  height limit; an additional linked description sheet aids reading beyond it.
- Synthetic Linux/Windows and unpacked frozen EXE smoke cover both formats and
  review states. Published version/source/hash are bound by immutable release gates.

Runtime remains offline, CPU-only, without LLM, OCR, GPU or network document APIs.
User visual Excel acceptance and geological resolution of source conflicts are
PENDING. Formulas are recalculated by Excel; Python does not compute caches.
Private documents and detailed audits are excluded from source, CI and handoff.
