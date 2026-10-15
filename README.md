# Skill Passport

Skill Passport builds an evidence-backed skills profile from public GitHub work and a resume PDF. AI may propose claims, but deterministic checks decide what is verified. Passports marked **DEMO DATA** use fictional resumes.

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

`backend/.env` must contain `GITHUB_TOKEN`, `GEMINI_API_KEY`, and `GEMINI_MODEL`. Optional: `FRONTEND_ORIGINS` (comma-separated) lists the frontend URLs allowed to call the API; it defaults to `http://localhost:5173`, so add your Vercel URL when deploying. Optional: `RECRUITER_KEY`; when set, the recruiter review endpoints (which include fraud signals) require it, and the recruiter pages ask for it once per browser tab. Leave it empty for local development, and set it for any public deployment. The existing `.env` is ignored by Git; do not replace it with `.env.example`. The current Gemini model setting is `gemini-flash-latest`.

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
- Recruiter evidence review (all fraud signals): <http://localhost:5173/recruiter/{passport-id}/evidence>
- Placement Cell: <http://localhost:5173/placement>
- FastAPI docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

Three fictional resume PDFs for local demonstrations are in `samples/`. Each includes a visible `DEMO DATA` label. Mark generated passports as demo data in the create form.

## Fraud detection demo

This shows the checks on three kinds of repository. Students never see these signals: `GET /passport/{id}` is student-safe, and the signals appear only on the recruiter pages, which use `/passport/{id}/review`.

**What is checked.** Only a **confirmed copy** is excluded automatically: two different cited lines found in another owner's repository created before the student's. Everything else is an advisory signal for a human to judge: line authorship, file creator, bulk upload, commit count, AI chat text, one-line code matches, commit spread, and history dated before the repository existed. Dependency and config files only count towards "Used" if the student committed to them.

### Setup

1. Use a spare GitHub account. A passport reads the account's **5 most recently pushed** non-fork repositories, so all three demo repos appear in one passport and their signals show up per repository. To see the Placement Cell ranking change, put each scenario in its own account, or compare the demo account against a genuine one.
2. `backend/.env` must contain a `GITHUB_TOKEN`. Line authorship (GraphQL blame) and code search don't work without one, and those signals then show as "not checked".
3. Create the three repositories below, then generate a passport for the account in the Create screen with a resume from `samples/` and **Mark this passport as DEMO DATA** ticked.
4. Open `/recruiter/{id}`, then the **Evidence** links (or `/recruiter/{id}/evidence`). After changing a repository, use **Re-check** on the student page; it bypasses the cache.

### (a) Genuine

A real project you built yourself: create the repository on GitHub first, then push several commits over several days, each touching the same source files.

Expected: claims verify and the signals pass, giving **High** verifiability. Some signals may show "not checked" (for example, code search rate limits); that doesn't lower the tier.

### (b) Clone with history

Clone a popular public repository (`git clone`), create a **new empty repository** (not a fork), and push the clone with its full history. Then make at least 3 small commits of your own that edit existing source files, dated before today, to simulate imported or backdated history (`git commit --date="2023-03-01T10:00"`), and push.

Expected:
- Evidence panel: "Your share of this file's commits … it was created by <original author>" and "Cited lines written by you" warnings.
- **History matches when the repository was created**: warns, because your commits are dated before GitHub created the repository. The wording stays neutral, because honest students sometimes build locally before pushing.
- If code search finds two cited lines in the original repository, the citation is also excluded as a confirmed copy, as in (c).

`history_import` looks only at *your* commits. A plain clone with no commits by you produces no code evidence at all: the files fail the "authored" check and appear only as rejected AI claims.

### (c) Copy-paste dump

Copy source code from a popular public repository (at least 15 source files, and at least 20 files or 2,000 lines in total) into a **new** repository, and push it as one "Initial commit".

Expected:
- **Built up over several commits**: warns (bulk upload, never edited afterwards).
- **Work spread over time**: warns ("All 1 of your commits in this repository were made on one day …").
- **Confirmed copy**: the cited code is found in the original, older repository. The citation is removed from the skill, its level is recalculated, and it appears under "Rejected and excluded claims" with a **View original** link.
- Verifiability is **Low**, the recruiter header shows "N copied citations excluded", and the student ranks lower in the Placement Cell.
- The student page only says "N citations were not counted after authorship review", with no file names or sources.

Code search skips lines that appear in more than 50 repositories (too common to judge), so a heavily cloned project may show "not checked" instead of a confirmed copy. A less-cloned public repository works more reliably.

## Tests

From the project root:

```powershell
python -m pytest backend/tests -q
cd frontend
npm run build
```
