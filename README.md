# Skill Passport

Skill Passport builds an evidence-backed skills profile from public GitHub work and a resume PDF. AI may propose citations, but deterministic checks decide which citations are verified. Levels show evidence of use in the candidate's code, not how well a skill is used. Passports marked **DEMO DATA** use fictional resumes.

## Run locally (Windows)

Start the backend in one PowerShell terminal:

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

HTTPS to GitHub uses the Windows certificate store (via `truststore`), so it also works behind antivirus or proxy software that intercepts HTTPS. Note that `--reload` restarts the server whenever a backend file changes, which cancels any passport being generated at that moment.

Always activate this `backend\.venv` before installing or running; packages installed into another Python (or the old `.venv` in the project root) won't be seen by the server. Check with `python -c "import httpx, truststore"`.

`backend/.env` must contain `GITHUB_TOKEN`, `GEMINI_API_KEY`, and `GEMINI_MODEL`. Optional: `FRONTEND_ORIGINS` (comma-separated) lists the frontend URLs allowed to call the API; it defaults to `http://localhost:5173`, so add your Vercel URL when deploying. Debug endpoints are disabled unless `ALLOW_DEBUG_ENDPOINTS=true` is explicitly set. Optional: `RECRUITER_KEY`; when set, recruiter review endpoints and "Mark reviewed" require it, and the recruiter pages ask for it once per browser tab. Passport creation returns an owner access token that the creating browser tab keeps in session storage; it is required for job polling and passport re-checks. The existing `.env` is ignored by Git; do not replace it with `.env.example`. The current Gemini model setting is `gemini-flash-latest`.

Start the frontend in a second PowerShell terminal:

```powershell
cd frontend
npm install
npm run dev
```

## Screens and API

- Create Passport: <http://localhost:5173/>
- Student Passport: <http://localhost:5173/passport/{passport-id}>
- Recruiter view: <http://localhost:5173/recruiter/{passport-id}>
- Recruiter evidence review (all signals, evidence records, review requests): <http://localhost:5173/recruiter/{passport-id}/evidence>
- Placement Cell: <http://localhost:5173/placement>
- FastAPI docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

Three fictional resume PDFs for local demonstrations are in `samples/`. Each includes a visible `DEMO DATA` label. Mark generated passports as demo data in the create form.

## Reading a passport

- **Levels** (evidence of use, not ability): **Unverified**, mentioned on the resume or a certificate with no evidence in the analysed files; **Used**, listed in a dependency or config file the candidate committed to; **Applied**, used in source code they committed to, with exact lines cited; **Demonstrated**, applied and exercised by a test the candidate committed to that targets that code (by file name or import). A citation counts only if git blame shows the candidate last changed at least one of its lines (unknown blame counts), and only one where they wrote at least half can make a skill Demonstrated.
- **Coverage.** Evidence comes from a sample: up to 5 repositories, 6 source files each, and about 8,000 lines sent to the AI step. The one-line summary ("Analysed 5 repositories · 28 of 168 source files · about 6,400 lines") expands to show what wasn't read and why. "Not found" means not found in the analysed files, not that the candidate lacks the skill.
- **Evidence packet (recruiter view).** Each requirement row is collapsed to its status, attribution and a one-line summary. Clicking it shows four parts:
  - **What supports it:** Met / Partial / Not found, plus up to 2 evidence links, code first.
  - **Attribution:** High, Medium or Low, for how clearly the cited work traces to the candidate (not their ability). A thin or one-day history, a bulk upload, backdated history, AI chat text or a one-line copy match caps it at Medium; an earlier-source match on the skill caps it at Low; too little history to judge shows "Low (limited history)".
  - **What's uncertain:** short lines such as "Only one citation" or an advisory warning.
  - **What to ask next:** the interview-kit question, or a template question when there isn't one.

  It is computed deterministically, with no extra AI calls.
- **Evidence record (recruiter evidence page and badge):** the commit, path, lines, SHA-256 of the cited lines, retrieval time, verifier version, checks and the rules that set the level.
- **Students** see every citation that wasn't counted, with links to their lines and the earlier source, and can **Request review**. They don't see the advisory signals or their thresholds. A review request never changes levels; a recruiter marks it reviewed.

## Regenerate all passports

After changing verification rules, rebuild every stored passport with a fresh GitHub read. This is the same as pressing Re-check on each one, and it reuses each passport's stored resume and certificate mentions. From the project root:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\recheck_all.py
```

It prints one line per passport (username, requirements met before → after, and rejected and excluded claim counts). A passport that fails, for example on a GitHub rate limit, is reported and left unchanged. Each passport makes live GitHub and Gemini calls, so expect about a minute per passport.

## Authorship checks demo

This shows the checks on three kinds of repository. Students never see the advisory signals: `GET /passport/{id}` is student-safe, and the signals appear only on the recruiter pages, which use `/passport/{id}/review`. Students do see any citation that wasn't counted, and can request a review.

**What is checked.** Only an **earlier public source** is excluded automatically. That requires two conditions: two different cited lines are in the same file in another owner's repository, and that file's first commit there is more than a day before the first commit of the candidate's file. File-level dates are compared, not repository creation dates. If either date is unknown, it stays a warning. An exclusion means "can't attribute to the candidate", not "copied"; it may be legitimate reuse.

Everything else is an advisory signal for a human to judge: line authorship, file creator, bulk upload, commit count, AI chat text, one-line code matches, commit spread, and history dated before the repository existed. Dependency and config files only count towards "Used" if the student committed to them.

### Setup

1. Use a spare GitHub account. A passport reads the account's **5 most recently pushed** non-fork repositories, so all three demo repos appear in one passport and their signals show up per repository. To see the Placement Cell ranking change, put each scenario in its own account, or compare the demo account against a genuine one.
2. `backend/.env` must contain a `GITHUB_TOKEN`. Line authorship (GraphQL blame) and code search don't work without one, and those signals then show as "not checked".
3. Create the three repositories below, then generate a passport for the account in the Create screen with a resume from `samples/` and **Mark this passport as DEMO DATA** ticked.
4. Open `/recruiter/{id}`, then the **Evidence** links (or `/recruiter/{id}/evidence`). After changing a repository, use **Re-check** on the student page; it bypasses the cache.

### (a) Genuine

A real project you built yourself: create the repository on GitHub first, then push several commits over several days, each touching the same source files.

Expected: claims verify and the signals pass, giving **High** verifiability and **High** attribution in the evidence packets. Some signals may show "not checked" (for example, code search rate limits); that doesn't lower the tier. Add a test file named after a module (for example `tests/test_engine.py` for `engine.py`) to see that skill become **Demonstrated**.

### (b) Clone with history

Clone a popular public repository (`git clone`), create a **new empty repository** (not a fork), and push the clone with its full history. Then make at least 3 small commits of your own that edit existing source files, dated before today, to simulate imported or backdated history (`git commit --date="2023-03-01T10:00"`), and push.

Expected:
- Evidence panel: "Your share of this file's commits … it was created by <original author>" and "Cited lines written by you" warnings.
- **History matches when the repository was created**: warns, because your commits are dated before GitHub created the repository. The wording stays neutral, because honest students sometimes build locally before pushing.
- Attribution in the evidence packets is **Medium** or **Low**.
- Because the clone keeps the original history, each file's first commit is the original author's, and it isn't earlier than "your" file's first commit (which is the same commit). So it is normally **not** excluded as an earlier source; the advisory warnings above are what flag it.

`history_import` looks only at *your* commits. A plain clone with no commits by you produces no code evidence at all: the files fail the "authored" check and appear only as rejected AI claims.

### (c) Copy-paste dump

Copy source code from a popular public repository (at least 15 source files, and at least 20 files or 2,000 lines in total) into a **new** repository, and push it as one "Initial commit".

Expected:
- **Built up over several commits**: warns (bulk upload, never edited afterwards).
- **Work spread over time**: warns ("All 1 of your commits in this repository were made on one day …").
- **Earlier public source**: the cited file in the original repository first appeared years before your "Initial commit", and two cited lines match. The citation is removed from the skill, its level is recalculated, and it appears under "Rejected and excluded claims" with "Matches code that appeared in {repo} on {date}, before this file's first commit here ({date})…" and a **View earlier source** link.
- Verifiability is **Low**, the recruiter header shows "N citations matched earlier public sources", and the student ranks lower in the Placement Cell.
- The student page lists each citation that wasn't counted (file, lines, links to their lines and the earlier source) with a **Request review** button. The note they send shows on the recruiter evidence page with **Mark reviewed**, and the recruiter header shows "N open review requests".

Code search skips lines that appear in more than 50 repositories (too common to judge), so a heavily cloned project may show "not checked" instead of an earlier source. A less-cloned public repository works more reliably. If the matching file was renamed in the original repository, its first-appearance date can't be found, and the match stays a warning.

## Tests

From the project root:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
cd frontend
npm run build
npm test
```

`npm test` uses Node's built-in test runner (Node 22.18 or newer, which runs TypeScript directly).

All GitHub and Gemini calls are mocked in the tests.
