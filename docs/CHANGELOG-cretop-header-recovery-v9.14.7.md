# v9.14.7 - CRETOP financial header recovery

Date: 2026-10-16 (Asia/Seoul)

Baseline: latest fetched `origin/main`, `12176af` (2026-10-15).
An isolated managed worktree preserves the original checkout and its unrelated changes.

## Cause

The PDF content stream can store a visually topmost year header after account rows.
The old parser only searched before the first account, losing the explicit year columns
while still reading financial amounts. The registration UI treated any extraction
warning as an unreadable-page warning, including a year-only review warning.

## Changes

1. Recover explicitly labelled year/date headers inside the selected statement,
   including headers emitted after account rows. Do not infer years from body amounts.
2. Support the account-name header and star annotations used in detailed statements.
3. Reject invalid dates, conflicting/duplicate years and misaligned amount columns.
   Stop at other statement/company/peer sections instead of borrowing their headers.
4. Separate page-processing failures from financial, identity and certification
   review warnings. Keep the explicit financial-review/save safeguard.
5. Show the latest three dated financial rows with thousands separators and distinct
   missing, zero and negative values. Explain original units and summary rounding.

The original summary-first source policy and raw values are unchanged. Missing loan,
advance and certification information is not fabricated as zero or a negative result.

## Changed files / overlay manifest

- `VERSION.txt`
- `RUN_v9.14.7-cretop-header-recovery.bat`
- `cretop_worker.py`
- `enterprise_registration_ui.py`
- `tests/test_cretop_reordered_headers.py`
- `tests/test_cretop_registration_ui.py`
- `docs/CHANGELOG-cretop-header-recovery-v9.14.7.md`

## Verification

- Local real-document validation with the production-pinned `pypdf==5.9.0`:
  all 31 pages processed as text; no OCR or external API; no extraction warnings;
  three annual rows restored; source years and summary amounts matched the PDF.
- 387 synthetic parser, financial signs, OCR, identity, registration UI, preservation,
  document registration, navigation and upload-security regression tests passed
  with the production-pinned PDF reader.
- Customer PDFs, identifiers and extracted customer data are not committed or packaged.

The real-document validation only reads the supplied PDF and holds the result in
memory. It does not save or modify customer records or financial snapshots.

## Compatibility and rollout

No Supabase table, column, RLS, migration, customer ID or Storage change is required.
No phone enrichment, sales assignment, login, tax-claim/Tilko or calling API logic changes.
The sidebar is unchanged: source inspection found one sidebar renderer, and a long
stitched screenshot alone does not establish repeated menu rendering.

The overlay ZIP contains only the seven listed source files at their original paths,
without a wrapper folder, `.git`, environment files or customer data. Back up these
source files before overlaying. The RUN file performs local synthetic tests only;
it does not install dependencies, push GitHub or deploy Railway.

After approved deployment, re-analyse the PDF. If the company already exists, review
the values and explicitly approve updating the existing company instead of creating
a duplicate. An uncertain financial result still requires the existing review checkbox.

Rollback: restore the backed-up seven source files or deploy the prior reviewed code
revision. Keep customer records and source documents; no database rollback is needed.

## GitHub handoff (not executed)

From the reviewed patch checkout, create a dedicated branch if HEAD is detached:

```bash
git switch -c codex/cretop-header-recovery
git status
git add VERSION.txt RUN_v9.14.7-cretop-header-recovery.bat cretop_worker.py enterprise_registration_ui.py
git add tests/test_cretop_reordered_headers.py tests/test_cretop_registration_ui.py docs/CHANGELOG-cretop-header-recovery-v9.14.7.md
git commit -m "fix: recover reordered CRETOP financial year headers"
git push -u origin codex/cretop-header-recovery
```

Review against the latest main and deploy only after explicit approval. Avoid `git add .`
in the original dirty checkout so unrelated work or customer files are not uploaded.
