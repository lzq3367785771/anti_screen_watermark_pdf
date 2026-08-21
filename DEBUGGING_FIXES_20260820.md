# DOCX watermark chain debugging record (2026-08-20)

## Reproduced failures

- `issue_watermarked_pages()` embedded the 140-bit payload with `repeat=16`
  (`2240` DCT units), while `docx_pipeline.py` wrote `repeat=6` to the
  manifest.
- The DOCX pilot manifest used legacy `bits` and omitted `enabled`, `version`,
  `bit_count`, and `position_offset`. `trace_document_photo()` therefore
  returned `NO_SYNC_PILOT` without attempting pilot synchronization.
- Failed DOCX issuance attempts left registry issues without an output or
  manifest. Those incomplete issues were still included in ML scoring and
  could outrank the real issued token.
- The wrong-key branch in `trace_document_photo()` referenced undefined debug
  variables, masking the intended key validation error with `NameError`.
- `key` and caller-supplied `key_id` were not checked by the shared carrier.
- DOCX rendering used one shared `.docx_render` directory and issuance had no
  output/manifest/registry rollback transaction.

## Fixes

1. Added manifest schema v2 and a single canonical carrier-config builder.
   PDF, PPTX, and DOCX manifests now consume the exact config returned by the
   embedding carrier.
2. Added schema-v1 normalization for historical DOCX manifests. Actual page
   DCT statistics recover payload repeat `16` and pilot repeat `6`; the missing
   pilot fields are reconstructed safely. Schema-v2 inconsistencies are
   rejected instead of silently repaired.
3. Added SHA-256-derived `key_id` helpers and strict `key`/`key_id` validation.
   Wrong keys now raise the intended `ValueError`.
4. Photo tracing now scores only completed issues for the aligned document and
   active key. Unattached/failed issues and issues made with another key are
   excluded.
5. For an ORB-aligned page with an accepted PN pilot, the conservative photo
   z-score floor is `3.0`; normalized score, margin, hard-match rate, observed
   bits, document alignment, key match, and pilot checks still apply.
6. DOCX issuance now reuses a verified Canonical Reference set or publishes a
   new staged render atomically. It writes output/manifest files transactionally
   and rolls back an unattached issue and owned files on failure.
7. The unified document dispatcher now routes DOCX input to the DOCX adapter;
   DOCX output remains a flattened raster PDF.

## Verification

- Unit suite: `86` tests passed, `1` Word-COM integration class skipped in the
  Codex sandbox because that account cannot load the project's pywin32 runtime.
- Historical real photo `test_docx/photo_page1_v5.png`:
  - pilot status: `ACCEPTED`
  - payload repeat: `16`
  - result: `ACCEPTED_REGISTRY_ML`
  - trace token: `fe678cdd7cebc3a7`
  - hard match: `0.635714`
  - normalized score: `0.351040`
  - z-score: `3.036064`
  - margin z: `2.052928`
- A new schema-v2 three-page DOCX issuance produced `2240` payload DCT units
  and `384` pilot DCT units per page, with pilot offset `2240`. Tracing its first
  generated watermarked page selected the newly issued token with z-score
  `7.909641` and hard match `0.8`.
- Supplying a wrong key to the historical-photo trace returns `ValueError`
  (`key_id` mismatch), not `NameError`.
