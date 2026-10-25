# Offline evaluation harness

Measures how the skill-assessment pipeline behaves on synthetic fixture
repositories with documented labels. Run from `backend/`:

```powershell
..\.venv\Scripts\python.exe -m scripts.run_eval          # text report
..\.venv\Scripts\python.exe -m scripts.run_eval --json   # machine-readable
```

## What it evaluates

1. **File sampling/selection** - the real `_ordered_candidates`,
   `_select_source_files` and `_extend_selection_for_targets` from
   `app/services/github_ingest.py` decide which fixture files are "read"
   (per-repo `MAX_SOURCE_FILES` plus the bounded target-skill second pass
   applies), and `_files_for_prompt` from `app/services/claim_proposer.py`
   applies the prompt line budget. Fixture `resume_skills` are passed as the
   retrieval targets, mirroring `build_passport`.
2. **Deterministic stages given specified claims** - `verify_claims`,
   `exclude_uncounted_citations` and `compute_level` from
   `app/services/verifier.py`, including dependency/config/test/git
   deterministic evidence and blame-based exclusion.

## What it does not evaluate

- **Gemini claim proposal.** Replaced by a stand-in that emits each fixture's
  `available_claims` for sampled files only - a perfect reader of the sampled
  files. Real LLM proposal recall is worse or different. No paid API calls
  are made.
- **copy_check earlier-source search.** Needs live GitHub code search.
- **Live end-to-end baseline** (real GitHub ingest + Gemini). Outstanding;
  requires the maintainer's laptop workflow.

Fixture scores are therefore not main's full-pipeline accuracy and not
real-world accuracy; they are reproducible measurements of the named
components on the named fixtures.

## Labels

Labels in `fixtures.py` are AI-authored expectations derived from this
project's own taxonomy (`app/data/skills.json`) and the documented Evidence
Ladder rules, without independent human review. They encode defined
detection and level-assignment behaviors, not real-person proficiency.
Leakage limits: the same author wrote the fixtures and the code under test,
so design bias remains; the `heldout` split is reported separately and is
never used to tune changes. A genuinely independent label review is future
work and needs a human reviewer.

## Metrics

Per split (tune / heldout / all), with sample sizes:

- **Detection**: precision, recall, F1 over labeled-positive skills
  (level Used or higher) versus absent skills.
- **Levels**: exact-match rate, within-one rate, over/underrated counts
  against the labeled Evidence Ladder level.
- **Absent-skill violations**: skills reported with evidence that labels say
  must stay undetected.
- **Sampling losses**: available claims whose evidence file never reached
  the prompt.

No single accuracy percentage is produced, by design.

## Adding fixtures

Each fixture declares an ingestion-shaped repository (or an `all_files` map
to exercise fetch selection), the claims a perfect reader could make,
resume/certificate mentions, and labels with a provenance note. Put tuning
cases in `split: "tune"` and cases that must stay untouched by tuning in
`split: "heldout"`.

## Deterministic signature scan

`verify_claims` adds rule-based code citations for framework/tool skills the
claim proposer never named (`_signature_code_evidence`): a fetched, authored,
non-test file whose lines contain the skill's signatures is cited like any
other code evidence and passes the same exclusion stage (blame attribution,
earlier-source). Language skills are excluded because their signatures match
nearly every file. Coverage is the fetched file sample only - a skill whose
evidence was never fetched is still missed (see challenge-deep-burial), so
this is not a repo-wide scan. The challenge-unproposed-skill fixture shows
the rescue: without the scan the skill is a detection miss; with it, the
authored Django models file is cited and leveled Applied.
