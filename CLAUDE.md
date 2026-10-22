# CLAUDE.md: Skill Passport (Proof, Not Claims)

This file is the single source of truth for this project. Read it fully before writing any code. Every decision below was made deliberately after research; do not change a decision without a clear reason, and if you do, record it in the "Decision log" at the bottom.

---

## 0. TL;DR for the developer

We are building **Skill Passport**, an AI system that turns a person's real work (GitHub repos, resume, certificates) into an **evidence-backed skill profile** where **every skill is linked to the exact file and lines that show it in use**. An LLM only *proposes* skills with citations; **plain deterministic code verifies** every citation. Skills without evidence are shown as "Unverified" and never counted. The profile is compared requirement-by-requirement with a target role, each gap becomes a **task on the student's own repo**, and re-checking the repo after the task **upgrades the skill automatically**.

Core line for the product and pitch: **"Every skill is a claim that must survive a check. Every gap becomes a task that creates new proof."**

---

## 1. Context: the competition

- **Problem statement:** IM-03, "Skill Passport: Verified Skills from Real Work".
- **Focus area:** Skill intelligence, Generative AI. **Category:** Software.
- **Who it's for:** students and job seekers, recruiters, and college placement cells.
- **Background (from the brief):** Resumes list skills but rarely prove them. A student's real abilities are spread across code repositories, project reports, portfolios and certificates. Recruiters can't check all of this quickly, and learners often don't know which skills they're missing for the role they want.
- **The challenge (from the brief):** Build an AI system that turns a person's real work into a verified skill profile, where every skill is backed by evidence and linked to a clear path for growth.

### Expected solution (from the brief, all must be covered)
1. Analyse a person's work: code repositories, project documents, a portfolio, or certificates.
2. Produce a skill profile in which every skill and level is linked to cited evidence (a project, a file, or a piece of work).
3. Compare the profile with a target role and show matches and gaps.
4. Generate a personalised learning plan for closing the gaps.
5. Offer a shareable "skill passport" view that a recruiter can read in under a minute.
6. Guard against invented claims: a skill without evidence must never appear as verified.

### Expected outcomes (from the brief)
- Skill passports for a few sample users built from public or sample data.
- Every claimed skill traceable to its evidence.
- A clear explanation of how well a person matches a given role, and why.

### Stages
| Stage | What judges expect |
|---|---|
| Stage 1: Idea & presentation | The skill framework, the data sources, how evidence is linked to each skill, and mock-ups of the passport. **PPT due midnight, Oct 11, 2026.** Screenshots of the real app are being used as the mock-ups. |
| Stage 2: Online prototype | A passport generated from one public code repository and a resume, with every skill linked to evidence. Must be **online** (deployed, live link). |
| Stage 3: 24-hour grand finale | Multiple data sources, role matching, learning plans, a recruiter view, and checks that prevent unsupported claims. |

Build so that the Stage 1 screenshots come from the same codebase that becomes Stage 2 and Stage 3. No throwaway prototype.

---

## 2. Why this project is different (research summary)

### 2.1 What already exists and why it falls short
- **GitHub scorers (GitRoll, CodePrint, etc.):** give opaque scores; a recruiter can't click from "82" to the code that justifies it.
- **Resume-vs-GitHub verifiers on GitHub (SkillGuard AI, CodeCred AI, Resume Claim Verifier):** check repo names, languages, topics, READMEs, or dependency files. A README can say anything; a dependency shows an import, not skill. Evidence is repo-level, not file/line-level. Some use "AI checks AI" critic agents instead of hard rules.
- **Career/gap tools (Candid, Jobscan-style ATS checkers):** good gap analysis and roadmaps, but never verify that skills are real; roadmaps are generic and not tied to the student's own code.
- **SkillProof AI (github.com/hxrrrrri/Skillbuilder_AI):** the closest competitor, likely built for a similar hackathon. Evidence-backed rubric scores, ownership tokens, validators that can only lower scores, publish gates. Weaknesses: scores general qualities (code quality, testing), not specific skills; no learning plan; GitHub only; very heavy and complex. **Do not copy its design.** Judges may know it.
- **Enterprise platforms (Eightfold, TechWolf, Beamery, Gloat):** use private HR data, black-box inference, not available to students.
- **Assessment platforms (HackerRank, CodeSignal, Workera):** test skill in a sandbox, not through real work. Remote assessments are increasingly broken by AI cheating (CodeSignal reported fraud attempts rising from 16% in 2024 to 35% in 2025).
- **LinkedIn verified skills:** usage-based badges from partner SaaS apps; vendor-locked and tool-fluency only. LinkedIn discontinued its skill quizzes partly over fraud.
- **Credential infrastructure (Open Badges 3.0, W3C Verifiable Credentials, Credly):** standard containers, but they verify *issuance*, not that the underlying evaluation was good.

### 2.2 Research findings that shape the design
- LLM skill extraction is mature but imperfect: e.g. SkiLLMo reached 91% precision on extraction but ~79% after normalising to ESCO; a Portuguese resume pipeline reached ~70.6% accuracy linking to ESCO. **Roughly 1 in 5 normalised skills can be wrong**, which is why verification must be deterministic code, not a prompt.
- Human vs GPT-4 resume ratings correlate only weakly (Vaishampayan et al., NAACL 2025): don't let the LLM "score" people.
- GitHub alone is an incomplete proxy for expertise (Montandon et al., MSR 2019): when evidence is missing, say "no evidence found", never "not skilled".
- Stars, followers and commit counts measure activity, not expertise (Dey et al., ICSE 2021).
- Recency and file size correlate with file knowledge (Cury et al., ESEM 2022): supports per-file authorship checks.
- Best pattern across the literature (JobMatchAI ACL 2026, Pruski et al. 2025, Clavié & Soulié 2023): **use AI to read and explain; use fixed rules to decide.**
- LLMs show demographic bias when judging GitHub profiles (Nakano et al., ICSME 2024): redact personal details before LLM calls.
- Citation-grounded generation (ALCE, Gao et al., EMNLP 2023) is the basis for "every claim cites evidence".
- "Skills-based hiring" mostly fails in practice (Harvard/Burning Glass: ~1 in 700 hires affected) because recruiters don't trust or use skill signals. **Trustworthy, checkable evidence is the missing piece.**

### 2.3 The gap we fill
No existing tool or paper combines all of: **file-level proof + ownership check + evidence-based levels + explainable role matching + a learning plan tied to the student's own repos that re-verifies itself**, in a view a recruiter can read in one minute. That combination is our novelty.

---

## 3. The innovations (all must exist in the product)

| ID | Innovation | What it means | Where it appears |
|---|---|---|---|
| I1 | **Verify-by-construction** | The LLM only proposes skills with citations (repo, file, line range). Deterministic Python code verifies each one. The LLM never decides verification. | Evidence panel with verification ticks |
| I2 | **Evidence Ladder** | 4 levels based on the type of proof. No numeric skill scores anywhere. | Skill grid colours + legend |
| I3 | **Ownership check** | Forks excluded; a file counts only if the student authored at least one commit touching it. | "Authored by you ✓", "Not a fork ✓" |
| I4 | **Rejected-claims transparency** | Every LLM proposal that fails verification is kept with the reason and shown in the UI. Shows the anti-hallucination guard working. | "Rejected AI claims (N)" section |
| I5 | **Explainable role match** | Requirement-by-requirement: met / partial / missing, each with a one-line reason. Summary is "X of Y requirements met". No black-box percentage. | Recruiter checklist |
| I6 | **Growth Loop** | Each gap becomes a concrete task on the student's own repo. "Re-check" re-ingests GitHub and upgrades skills (e.g. "Docker: Claimed → Used"). Learning creates proof. **Our biggest novelty.** | Growth Plan + Re-check |
| I7 | **Defend-your-work interview kit** | 2 questions per verified skill generated from the student's actual cited code, plus "a good answer mentions…". Exposes copied/AI-generated code. | Recruiter view |
| I8 | **Recruiter one-minute view** | Everything a recruiter needs on one screen, no scrolling at 1440x900. | Recruiter page |
| I9 | **Placement cell dashboard** | Batch heatmap (students × required skills) + most-missing skills → suggested workshops. The brief names placement cells; almost nobody serves them. | Placement page |
| I10 | **Portable credential** | Export as Open Badges 3.0-style JSON-LD with evidence URLs. Marked "unsigned prototype"; no fake signatures. | Download button |
| I11 | **Bias guard** | Remove name, email, phone, address, gender, age, photo text and college name from resume text before any LLM call. | Architecture slide; resume service |
| I12 | **Fraud checks, hidden from students** | Confirmed copies are excluded; authorship and timing signals are shown to recruiters only (see 3.2). | Recruiter evidence review; Placement Cell ranking |

### 3.1 Evidence Ladder (exact definitions, implemented as rules, never by the LLM)
| Level | Rule | Colour |
|---|---|---|
| **Claimed** (shown as "Unverified") | Mentioned on the resume or a certificate; no evidence in the analysed files. | Grey `#8A8F9C` |
| **Used** | Listed in a dependency or config file **the student committed to** (e.g. `flask` in their requirements.txt, their Dockerfile), in a test file they committed to (Unit Testing only), or in their commit history (Git). Files they never touched stay attached but don't count. | Amber `#D99A00` |
| **Applied** | Used in source code the student committed to: at least one verified citation (exact lines). | Green `#2FB36B` |
| **Demonstrated** | Applied AND at least one of the skill's counted citations (line share ≥50% or unknown) is targeted by a test **the student committed to** (`test_authored`; unknown = no upgrade) in the same repo: by **name** (`engine.py` → `test_engine.py`, `engine_test.py`, `engine.test.js`, `engine.spec.ts`) or by **import** (`from x.engine import`, `require('./engine')`, `from '../engine'`). Stems under 3 characters and generic stems (`__init__`, `index`, `main`, `utils`, `app`) never match. CI and Dockerfiles don't upgrade other skills (shown as "Build or deploy config present"). The triggering test is stored as `demonstrated_by`. | Dark green `#1E7A46` |

Certificates add a "Certified" tag to a skill but never raise its level on their own.
Levels show evidence of use in the candidate's code, not how well the skill is used. "Verified" describes citations and evidence ("verified citation"), never a person's ability. The UI wording lives in `frontend/src/levels.ts` and `LEVEL_DESCRIPTIONS` in `verifier.py`.

### 3.2 Fraud and authorship checks
Only two things change what counts; everything else is an advisory signal for a human.

| Check | Effect | Rule |
|---|---|---|
| Signature check (`signature_found`) | Hard check (claim rejected) | Language skills are evidenced by file extension. Frameworks, tools and practices need a real code signature within ±5 lines of the citation (whole-word start, case-insensitive). |
| Earlier public source | Citation **excluded** (`kind: "earlier_source"`; older passports: `"copied"`) | A distinctive cited line is found in another owner's non-fork repo, a second, different line from the same citation is in **that same file**, and that file's first commit is **more than 1 day before** the candidate's file's first commit (file-level dates, never repo creation dates). Unknown dates stay a warning. **Applies per file:** every other code citation of the same repo + file is excluded the same way (`propagate_earlier_sources_per_file()`, same reason prefixed "Excluded because another citation in this file (lines N-M) matched an earlier public source.", same source link; no extra searches); a one-line advisory match never spreads. Level recomputed. Means "can't attribute to the candidate", not "copied". |
| Authorship of dependency/config/test files | Counts towards Used only if authored | `authored_paths` or `test_authored` from ingestion; missing = not authored. |
| `line_share` (git blame) | 0 cited lines by the candidate: citation **excluded** (`kind: "not_written_by_candidate"`); under 50%: counts for Applied, never Demonstrated | Unknown blame (no blame, or any cited line from an author not linked to a GitHub account) has no effect. Shown as "Cited lines written by you: N of M". |
| `commit_share`, `bulk_upload`, `repo_history`, `ai_leftover` | Advisory | Per file (see Decision log, 2026-10-11). |
| `copy_match` (one line only, or dates unknown) | Advisory | One shared line is often boilerplate or a tutorial; label "Earlier public source". |
| Demonstrated test targeting | Level rule | A test must target the cited file by name or import (section 3.1); CI/Dockerfiles never upgrade other skills. |
| `commit_spread` | Advisory, per repo | Warn when all the user's commits fall on one UTC day in a repo with ≥15 source files. |
| `history_import` | Advisory, per repo | Warn when ≥3 commits and at least half are dated >7 days before GitHub's `created_at`. Neutral wording: honest students sometimes build locally first. |
| `account_ownership` | **Hard gate** for non-demo passports | `POST /ownership/challenge` issues a one-time code (`skill-passport-verify-…`, 1 hour, single use); the applicant creates a **public** gist with that code as its description from the account, and `/passport/create` (`ownership_challenge_id`) checks the account's recent gists before the job starts. This establishes control of the GitHub account only, not the applicant's real-world identity. No proof → 403 and no job. The proof is recorded against that one passport (`ownership_challenges.passport_id` = the creation job id) and never counts for another passport of the same username. `REQUIRE_OWNERSHIP_PROOF=false` turns the gate off in development only: startup refuses it when `APP_ENV` isn't `development`, and refuses unrecognised values (which keep the gate on). Demo passports skip it. Stored per passport in `identity` (ok = this passport's own proof, with date and gist link; unknown = demo, always; warn = created without a proof). Recruiter-only. Expired unused challenges are pruned when a new challenge is issued; verified rows remain to support this signal. |
| `resume_github_link` | Advisory, recruiter-only | GitHub usernames from `github.com/<name>` links in the raw resume text (only the usernames are kept; reserved paths like `orgs/` ignored). ok = links to this account; warn = links only to other accounts (could be an organisation or collaborator; ask the candidate); unknown = no links. Unchanged on re-check (the resume isn't stored). |
| `backdated_commits` | Advisory, per repo | Warn when ≥3 of the user's commits and at least half have a committer date >7 days after the author date. Neutral wording: rebases and cherry-picks do this too, and an informed user can set both dates. |

**Student view rule: internals hidden, outcomes shown.** Advisory signals, thresholds, evidence records and interview answers are **never** returned by student endpoints (see section 9). Outcomes that affect the student are shown: every excluded citation (with links to their lines and the earlier source, and a neutral reason), and they can request a review (`review_requests`; never changes levels or exclusions; recruiters mark requests reviewed). An earlier-source exclusion makes verifiability "Low". Student-facing text never says "fraud", "fake", "copied" or "cheat".

---

## 4. Hard rules (never violate)

1. No skill is shown as verified without passing **all** code checks.
2. Unverified (Claimed) skills are always shown separately, in grey, and never counted as "met" in role matching.
3. No numeric skill scores ("Python: 85") anywhere in the UI or API.
4. The LLM never decides verification, levels, or role-match status. Those are deterministic code.
5. Any demo or sample data is visibly labelled **"DEMO DATA"**. Never present fake data as real.
6. Every evidence item links to the exact GitHub lines:
   `https://github.com/{owner}/{repo}/blob/{sha}/{path}#L{start}-L{end}`
7. Personal details are redacted before any LLM call (I11).
8. Don't claim the system "proves" skills with 100% certainty. It is a transparent evidence-retrieval system with human-checkable trails.
9. Detector internals (integrity signals and thresholds, evidence records, interview-kit answers) are never returned by student-facing endpoints; outcomes that affect the student (excluded citations) are always shown, with a way to request a review.

---

## 5. Tech stack (decided, don't change mid-build)

- **Backend:** Python 3.11, FastAPI, httpx, pdfplumber (layout-aware PDF parsing; handles two-column resumes), google-genai (Gemini), pydantic, SQLite.
- **Frontend:** React + Vite + TypeScript + Tailwind CSS + react-router + recharts.
- **LLM:** Gemini Flash (current model, name set via `GEMINI_MODEL` in `.env`). Optional later: Groq as a fallback provider.
- **External APIs:** GitHub REST API with a personal access token (classic, read-only public repos). Unauthenticated limit is only 60 requests/hour.
- **Ports:** backend 8000, frontend 5173 (CORS enabled for 5173).
- **Environment (`backend/.env`):** `GITHUB_TOKEN`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `APP_ENV`, `RECRUITER_KEY`, `TRUSTED_PROXY_IPS`. Default to production; set `APP_ENV=development` explicitly for local work. All non-development environments require `RECRUITER_KEY` at startup. Configure only actual reverse-proxy IPs in `TRUSTED_PROXY_IPS`.
- **Later deployment (Stage 2):** Vercel for the frontend; Render or Railway for the FastAPI backend (background jobs + SQLite don't fit Vercel functions). Firebase is **not** needed.

---

## 6. Project structure

```
skill-passport/
  CLAUDE.md                      (this file)
  backend/
    app/
      main.py                    FastAPI app, CORS, routes
      config.py                  loads .env
      data/
        skills.json              skill framework (~30 skills)
        roles.json               4 target roles
      services/
        framework.py             loads skills/roles
        github_ingest.py         P1
        resume_parse.py          P2 (+ I11 redaction)
        claim_proposer.py        P3 (LLM)
        verifier.py              P4 (NO LLM) <- core
        role_match.py            P5 (NO LLM)
        growth_and_kit.py        P6 (LLM, one call)
        badge.py                 P7 export
        storage.py               SQLite
        jobs.py                  background job status
      cache/                     disk cache for GitHub + Gemini
    tests/
      test_verifier.py
    requirements.txt
    .env.example
  frontend/
    src/
      pages/ CreatePassport.tsx, StudentPassport.tsx, RecruiterView.tsx, PlacementCell.tsx
      components/ LadderPill.tsx, EvidencePanel.tsx, GrowthCard.tsx, ...
      api.ts
  samples/                       DEMO resume PDFs (clearly labelled)
```

---

## 7. Data definitions

### 7.1 `skills.json` (~30 skills)
Fields per skill:
- `id` (e.g. `"python"`), `name`, `category` (Language / Framework / Database / DevOps / Practice / ML)
- `esco_label` (closest ESCO skill name, as a cross-reference for credibility)
- `aliases` (for resume matching, e.g. `["py", "python3"]`)
- `file_extensions` (for language skills, e.g. `[".py"]`)
- `dependency_names` (package names in requirements.txt / package.json / pom.xml / build.gradle / pyproject.toml)
- `code_signatures` (strings/regex showing usage in source, e.g. `"from flask import"`, `"@app.route"`, `"import React"`, `"SELECT "`)
- `config_files` (presence shows usage, e.g. `"Dockerfile"`, `".github/workflows/"`, `"docker-compose.yml"`)

Skills: Python, Java, JavaScript, TypeScript, C++, Go, Flask, Django, FastAPI, Spring Boot, React, Node.js/Express, Next.js, SQL, PostgreSQL, MySQL, MongoDB, Redis, Docker, Kubernetes, CI/CD (GitHub Actions), Git, REST API design, Unit Testing, PyTorch, TensorFlow, scikit-learn, Pandas, AWS, Linux/Shell.

**Why our own compact list + ESCO cross-reference:** ESCO is free and official but weak on modern tech; Lightcast is better for tech but its free API tier is tiny; SFIA's levels measure workplace responsibility, so nearly every student lands at level 1–2. A compact list mapped to ESCO gives credibility without those problems.

### 7.2 `roles.json` (4 roles)
Backend Developer Intern, Frontend Developer Intern, Full-Stack Developer Intern, ML Engineer Intern. Each has 8–10 requirements:
```json
{ "skill_id": "docker", "importance": "must" | "nice", "min_level": "Used" | "Applied" | "Demonstrated" }
```

### 7.3 Proposed claim (LLM output, untrusted)
```json
{ "skill_id": "flask", "repo": "flask-api", "file_path": "app/routes.py",
  "start_line": 12, "end_line": 88, "explanation": "Defines REST endpoints with Flask routing." }
```

### 7.4 Verified evidence item
```json
{ "type": "code" | "dependency" | "config",
  "repo": "flask-api", "file_path": "app/routes.py", "start_line": 12, "end_line": 88,
  "commit_sha": "a3f9c21", "url": "https://github.com/.../blob/<sha>/app/routes.py#L12-L88",
  "snippet": "...", "explanation": "...",
  "checks": { "file_exists": true, "lines_valid": true, "authored": true, "not_fork": true, "signature_found": true } }
```

### 7.5 Passport object (stored in SQLite as JSON)
```json
{ "id": "...", "created_at": "...", "github_username": "...", "target_role": "...",
  "sources": { "repos": 5, "resume": true, "certificates": 1 },
  "skills": [ { "skill_id": "python", "level": "Demonstrated", "certified": false,
                "evidence": [ ... ], "resume_mention": "line text or null" } ],
  "rejected_claims": [ { "claim": { ... }, "failed_checks": ["authored"], "reason": "..." } ],
  "role_match": { "met": 7, "total": 10,
                  "requirements": [ { "skill_id": "...", "status": "met|partial|missing",
                                      "student_level": "...", "min_level": "...", "reason": "..." } ] },
  "growth_tasks": [ { "skill_id": "docker", "target_repo": "flask-api",
                      "task": "Add a Dockerfile and docker-compose.yml...", "evidence_it_will_create": "..." } ],
  "interview_kit": [ { "skill_id": "...", "questions": [ { "q": "...", "good_answer_mentions": "..." } ] } ],
  "is_demo": false }
```

### 7.6 Custom roles (from a job description)
A student can paste a job description instead of choosing one of the 4 fixed roles (`ENABLE_CUSTOM_ROLES`, default true; false → `POST /roles/from-description` returns 404 and the option is hidden).
- Input: job title (1-120 chars) and description (50-8,000 chars). Recruiter contact details (emails, phone numbers) are removed before the Gemini call. **The description is never stored.**
- Candidates come from two sources: (a) the resume matcher (`find_skill_mentions()` in `resume_parse.py`, same names/aliases/rules), quoting the matched words with up to 60 characters of context from the same sentence and line (cut at `. ; ! ?` and line breaks; "." only before whitespace, so `Node.js`/`3.11` stay whole); (b) one Gemini call (`jd_parse.py`) for paraphrased skills, `{"requirements":[{"skill_id","importance":"must"|"nice","quote"}]}`, restricted to framework ids. For a skill found by both, the exact-match quote wins; Gemini only supplies its importance.
- Importance: Gemini's, else "must" when the quote contains required/must/essential/strong, else "nice".
- **Minimum level is set by code, never by Gemini:** nice → Used; must → Applied; must + a testing word in the quote (test, tests, tested, testing, unit test, TDD, pytest, jest) → Demonstrated.
- Validation (`claim_validation.py`, shared with reports): unknown skill id or a quote not found in the description (case- and whitespace-insensitive) → `dropped` with a reason. One requirement per skill (the stricter wins: must over nice, then the higher level); must-haves first; cap 12; at least 1 must-have or a 422. When Gemini can't help (unavailable after the existing retries: overload/503, timeout, network, quota, missing key; or invalid JSON after one retry), extraction still succeeds (201) from exact matches only, with the keyword importance rule and the same level rules, validation, dedupe and cap; the response carries `"mode": "exact_only"` (else `"ai"`) and the UI shows "AI suggestions unavailable right now; showing requirements found by exact matching."
- Stored in `custom_roles` (`id` = `custom-<uuid>`, title, requirements with quotes, dropped, created_at). `/passport/create` accepts that id as `target_role`; the role object (same shape as `roles.json`) goes to `match_role`, and the passport keeps it as `custom_role`, so Re-check reuses it instead of `roles.json`. Role matching rules are unchanged.
- Recruiter view shows "Custom role from job description: <title>" and each requirement's quote. Placement Cell evaluates against the fixed roles; custom-role passports appear under their own title in the preferred-role filter.

### 7.7 Reports and portfolios
Optional extra sources on `/passport/create` (`ENABLE_REPORTS`, default true; false → the inputs are ignored and hidden): up to 3 `reports` (PDF/PNG/JPEG, 10 MB each, counted in the 25 MB upload cap) and a `portfolio_url` (https only, ≤300 chars). Anything else → 422. **Reports and portfolios never raise a level; only code evidence does.**
- `report_parse.py`: text via the certificate extractor (`extract_certificate_text()`, hosted OCR for scanned pages; same errors as certificates, step "Reading reports"). Personal details are redacted (I11), then one Gemini call per report proposes `{"claims":[{"skill_id","quote"}]}`; `claim_validation.py` keeps known ids whose quote is in the report, one per skill (first quote), max 15, and records the rest in `dropped` with reasons. If Gemini is unavailable the report is kept with no claims and an `error` note. GitHub repo links (`github.com/<owner>/<repo>`) are taken from the raw text by regex; no API calls. The report text is not stored, only the filename.
- After verification and exclusion (levels final), `passport["reports"]` lists each claim as `corroborated` (the skill has counted evidence; `evidence` points to the strongest item: tested code, then code, commit, config, dependency) or `not_evidenced`. Not-evidenced claims are added as unverified mentions (`report_mention` on a `Claimed` skill, like resume mentions), so they never satisfy a requirement. Repo links are `own` (owner = GitHub username, case-insensitive) or `other_account`.
- `passport["portfolio"] = {url, status}`: `own_github_pages` only when the host is exactly `<username>.github.io` (case-insensitive), else `external_unverified`. The URL is **never fetched**.
- Re-check keeps each report's claims and quotes and recomputes their statuses against the new evidence; the portfolio is kept as is.
- Student view: the same claims, neutral wording ("corroborated" / "not yet evidenced"); `other_account` links and `report_mention` are removed (recruiter-only). Reports don't change the verifiability tier.

---

## 8. Pipeline (in order)

**P1. Ingest GitHub** (`github_ingest.py`)
- List public repos; **exclude forks**; sort by `pushed_at`; keep the top 5.
- For each repo: full file tree (`git/trees/{branch}?recursive=1`); dependency files (requirements.txt, package.json, pom.xml, build.gradle, pyproject.toml); flags for Dockerfile, docker-compose, `.github/workflows/*`, tests (`tests/`, `test_*`, `*.test.*`).
- Up to 6 important source files per repo (skip node_modules, dist, build, vendor, migrations, lock and minified files; max 600 lines; files the user changed in their 10 latest commits first, then prefer `src/` and `app/`; prefer files with ≥15 lines; one slot for a test file). Store contents with line numbers.
- Authorship per fetched file: `GET /repos/{owner}/{repo}/commits?path={file}&author={username}&per_page=5`; store SHAs. A file is "authored" if at least one commit by the user touches it.
- Authorship per dependency/config file (same call) → `authored_paths: {path: bool}`; test files aren't checked.
- The user's commits per repo (`commits?author={username}&per_page=100`) → `user_commits: [{sha, author_date, committer_date}]`, `user_commits_complete`; plus `source_file_count`.
- Disk cache keyed by username, with `schema_version: 3` (files with an older or missing version are expired and refetched); handle rate limits, 404s, empty repos.

**P2. Ingest resume and certificates** (`resume_parse.py`)
- Resume PDF: pdfplumber with layout-aware extraction.
- Certificates: text PDFs are extracted locally; scanned PDF pages and PNG/JPEG images use Google Cloud Vision OCR through backend Application Default Credentials. OCR output is used only for deterministic skill-name matching; only matched skill ids reach the LLM. Bound scanned PDF OCR to 10 pages per certificate.
- Match skill names/aliases (case-insensitive, word boundaries); keep the matched line as the resume mention.
- Certificates: same matching; mark skills `certified: true`.
- Apply the I11 redaction function before any text reaches the LLM.

**P3. Propose** (`claim_proposer.py`, **one** Gemini call)
- Input: ingested files *with line numbers*, resume skills, skill ids/names only.
- Structured JSON output validated by pydantic; retry once on invalid JSON.
- Prompt rules: only framework skill ids; only cite provided files; most specific line range; max 3 citations per skill; never invent files or lines.
- Output is a **proposal only**.

**P4. Verify** (`verifier.py`, **NO LLM**, the core)
For each claim, record pass/fail + reason for:
1. `file_exists`: repo and path exist in the ingested tree
2. `lines_valid`: line range within file length
3. `authored`: the user has at least one commit touching the file (store SHA)
4. `not_fork`: repo is not a fork
5. `signature_found`: for Language skills, the file extension matches; for every other skill, the file extension is allowed (if the skill lists any) **and** a code_signature appears within the cited lines (±5 line window). Extension alone is never evidence of a framework.

All 5 pass = verified; otherwise move it to `rejected_claims` with reasons (`kind: "unsupported"`).
Then add deterministic evidence the LLM may have missed: dependency matches → Used; config files (Dockerfile → Docker, workflows → CI/CD, tests → Unit Testing). Dependency/config evidence sets `checks.authored` from `authored_paths`.
Compute ladder levels per section 3.1 with `compute_level()` (only authored dependency/config evidence counts).
Each verified code citation gets advisory integrity signals (section 3.2), including the repo-level `commit_spread` and `history_import`.

**P4b. Earlier-source check** (`copy_check.py`, then `exclude_uncounted_citations()` in `verifier.py`, which also excludes citations with 0 candidate-written lines)
- Order: verify → `add_copy_signals` → `exclude_uncounted_citations` → coverage → role match → growth/kit, so role matching sees post-exclusion levels.
- Code search for the longest distinctive cited line (max 8 searches per passport). For each match in another owner's non-fork repo, compare when **that file** first appeared there (oldest commit via the `Link rel="last"` page) with the candidate's file's `first_commit_date`. If the other file is more than 1 day earlier and a second, different cited line is in that same file (read directly), it's an earlier source (`earlier_source`, `source_repo`, `source_url`, `source_first_seen`, `candidate_first_seen`). Unknown dates (path history doesn't follow renames) or a later date never exclude.
- Earlier-source citations are removed from the skill, added to `rejected_claims` (`kind: "earlier_source"`, `failed_checks: ["earlier_source"]`, `url`, `source_url`, neutral reason), and the level is recomputed; a skill left with no evidence is removed unless the resume or a certificate mentions it (then Claimed).
- Every code citation carries an evidence `record` (Decision log 2026-10-15); each requirement gets a reviewer packet in the review responses (`packets.py`).

**P5. Role match** (`role_match.py`, **NO LLM**)
- met = student level ≥ min_level; partial = skill exists but below min_level; missing = no evidence (including Claimed-only).
- Reason per requirement, e.g. "Requires Applied, found Used (flask in requirements.txt only)".
- Summary "X of Y requirements met"; must-have gaps first.

**P6. Growth tasks + interview kit** (`growth_and_kit.py`, **one** Gemini call)
- Growth: one task per partial/missing requirement, tied to the student's real repo names and contents; include `evidence_it_will_create` (e.g. "Dockerfile → Docker becomes Used; with existing code → Demonstrated").
- Interview kit: top 5 verified skills (Applied/Demonstrated), 2 questions each, referencing the actual file and lines, plus "a good answer mentions…".

**P7. Store and export**
- Save the passport to SQLite.
- `badge.py`: Open Badges 3.0-style JSON-LD; one achievement per verified skill; evidence entries with GitHub line URLs and check results; note `"unsigned prototype"`. Do not fake a cryptographic signature or invent DID methods.

**Re-check (Growth Loop):** re-run P1 bypassing the cache, then P4–P6 as a background job; return its `job_id` and polling token, then expose the student-safe passport and level changes when the job finishes.

---

## 9. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/roles` | Role list |
| GET | `/features` | Optional features enabled on this server (`custom_roles`, `reports`) |
| POST | `/roles/from-description` | JSON `{title, description}` → stored custom role (section 7.6); 10 per 10 min per IP; 422 on invalid input or no must-have, 404 when disabled; if Gemini is unavailable, 201 with `"mode": "exact_only"` |
| POST | `/passport/create` | multipart: `github_username`, `resume` (PDF), `certificates[]` (optional PDF/PNG/JPEG), `target_role` (fixed role id/name or a stored `custom-<uuid>`), optional `reports[]` (≤3 PDF/PNG/JPEG) and `portfolio_url` (https, ≤300 chars) → returns `job_id`; 26 MB request-body cap before multipart parsing |
| GET | `/jobs/{id}` | Step status: Reading GitHub → Reading resume → Proposing skills → Verifying evidence → Matching role → Building growth plan → Done |
| GET | `/passport/{id}` | Student-safe passport (`student_view`: no integrity signals, evidence records, good-answer notes, or stored resume/certificate mentions; every excluded citation listed with `citation_key`, links, neutral reason and its review-request status; `coverage` included) |
| GET | `/passports` | All passports, student-safe |
| GET | `/passport/{id}/review` | Full passport for recruiters, plus `review_requests` and `requirement_packets` (needs `X-Recruiter-Key` when `RECRUITER_KEY` is set) |
| GET | `/passports/review` | All full passports (Placement Cell); same key rule |
| POST | `/passport/{id}/review-requests` | Student asks for a review of an excluded citation: `{citation_key, note}` (≤1,000 chars) with `X-Passport-Token`; ownership is checked against the stored token hash, and the ≤10 total / one-open-per-citation rules are atomic |
| POST | `/review-requests/{id}/mark-reviewed` | Recruiter marks a request reviewed; same key rule as the review endpoints |
| POST | `/passport/{id}/recheck` | Starts background Growth Loop re-verification; returns `job_id` and `access_token` for status polling |
| GET | `/passport/{id}/badge` | Open Badges JSON-LD, including each code citation's `evidenceRecord` |
| GET | `/debug/github/{username}` | Dev only: ingest summary |
| POST | `/debug/resume` | Dev only: resume extraction |

The pipeline takes 30–90 s, so `/passport/create` runs as a background job.

---

## 10. Screens

**S1. Create Passport** (`/`)
GitHub username, resume drag-and-drop, optional certificates, role dropdown, "Generate Passport". Live step tracker polling `/jobs/{id}`. Tagline: *"Proof, not claims. Every skill backed by your real work."* Redirects to S2 on completion.

**S2. Student Passport** (`/passport/:id`)
- Header: username, target role, sources ("GitHub: N repos · Resume PDF · N certificates"), date.
- Evidence Ladder legend with one-line definitions.
- Skill grid coloured by level. Separate grey section: "On your resume, but no evidence found yet".
- Skill side panel: each evidence item (repo / file / lines, code snippet with line numbers, commit SHA, "Open on GitHub"), verification checklist ticks (File exists, Lines valid, You committed to this file, Not a fork, Skill found in code) plus "Cited lines written by you: N of M".
- Growth Plan cards with an "I've done this, re-check" button; highlight upgrades.
- Collapsible "Rejected AI claims (N)" with reasons.
- Buttons: "Recruiter view", "Download credential (JSON)".

**S3. Recruiter one-minute view** (`/recruiter/:id`), must fit 1440x900 with no scrolling
- Top: candidate, target role, large "Role match: X of Y requirements".
- Left 60%: requirement checklist (status icon, level pill, reason, "View evidence" link).
- Right 40%: top 5 verified skills; grey "Claimed but unverified" card ("Listed on resume. No evidence found in submitted work."); collapsible interview kit.
- Footer: "Every verified skill links to code this candidate authored. Unverified claims are never counted."
- "Copy share link" button.
- Uses the review endpoint; header shows verifiability and "N copied citations excluded". Every "Evidence" link opens **S3b**.

**S3b. Recruiter evidence review** (`/recruiter/:id/evidence`)
Skill list + evidence panel with all integrity signals, and "Rejected and excluded claims" with full details and "View original" links for confirmed copies. Not shown to the candidate; the student passport (S2) shows no signals, only unsupported AI claims plus a one-line review summary.

**S4. Placement Cell** (`/placement`)
Evaluation-role selector (the role being assessed) and a separate preferred-role filter (the student's saved target role); student count; average requirements met; heatmap (students × required skills, coloured by level, grey = missing); bar chart of % of students missing each skill; "Suggested workshop: <top gap>"; links to each passport and recruiter view. Student passport links can carry the saved role preference into the Placement Cell filter.

### Design system
White background; navy text `#1B2340`; accent orange `#F26B1D`; ladder colours from section 3.1; font Inter; rounded cards, generous spacing, consistent pills. Nav: "Skill Passport" logo + Create · Student · Recruiter · Placement Cell. No console errors; clean at 1440x900; loading and empty states everywhere.

---

## 11. Key decisions and rejected alternatives

| Decision | Chosen | Rejected and why |
|---|---|---|
| Who verifies | Deterministic code | LLM "critic agent" (AI checking AI is still an opinion) |
| Skill levels | Evidence Ladder by proof type | Numeric scores (unverifiable, opaque); SFIA levels (measure workplace responsibility, so all students land at 1–2) |
| Taxonomy | Own ~30 skills + ESCO labels | Raw ESCO (stale on tech), Lightcast (tiny free tier) |
| Role matching | Requirement-by-requirement rules with reasons | Cosine similarity alone (says Flask ≈ Django, can't say whether a requirement is met) |
| Architecture | Hybrid: deterministic extraction + LLM reasoning + deterministic verification | Rules-only (too shallow); Neo4j graph system (too heavy for a hackathon) |
| Authorship | Per-file commits by the user; forks excluded | Trusting repo ownership alone |
| AI-slop detection | Out of scope (future work) | Halstead-volume heuristics (unproven) |
| Evaluation | Hand-check ~50 claims across 3–4 sample users and report accuracy | Running workrb benchmarks (they test job-post extraction, not GitHub evidence) |
| Credential | Open Badges 3.0-style JSON, marked unsigned | Fake signatures or invented identifiers like `did:github` |
| Code reuse | Build our own; reuse libraries (pdfplumber, recharts) and small MIT-licensed helpers with credit | Forking an existing full project (plagiarism risk, judges may recognise it, doesn't contain our innovations) |
| Infra | Local SQLite; later Vercel + Render | Firebase (not needed) |

---

## 12. Current status (at handoff, Oct 11, 2026)

- Research complete: market, open-source repos, academic papers, and an external deep-research report were reviewed. The design in this file is final.
- Implementation: **started or about to start**. Check the repo to see what exists. If code exists, compare it against this file and continue from the first incomplete build step in section 13.
- Immediate deadline: **Stage 1 PPT with real app screenshots, due midnight Oct 11, 2026.** Prioritise getting S2 and S3 working with real data over everything else.

---

## 13. Build order (next steps)

Do these in order. Test after each step. Keep changes minimal; don't rewrite working files.

1. **Scaffold** (section 6) + `skills.json` + `roles.json` with complete content + loader. Backend `/health`; frontend nav + empty pages. Provide run commands.
2. **Ingestion:** `github_ingest.py` (P1) + `resume_parse.py` (P2 with redaction) + debug endpoints. Test on `octocat`.
3. **Core:** `claim_proposer.py` (P3) + `verifier.py` (P4). Write pytest tests for: a valid claim, a hallucinated file, out-of-range lines, a file not authored by the user, a fork repo, a resume-only skill, a Dockerfile-only skill. All must pass.
4. **Matching + growth + kit:** `role_match.py` (P5) + `growth_and_kit.py` (P6).
5. **API + jobs + storage + badge** (section 9, P7). End-to-end test with the owner's GitHub username and a DEMO resume.
6. **Frontend S1 + S2.**
7. **Frontend S3 + S4.**
8. **Real test + polish:** create 3 fictional DEMO resumes in `/samples`, each claiming real skills plus 1–2 skills absent from the code (e.g. Docker, AWS); generate passports for 3 real public GitHub users (Backend role, plus one ML role); confirm no unverified skill is shown as verified, line links open the right lines, rejected claims appear when they occur, and the placement view shows all students. Fix bugs only.
9. **Growth Loop live proof:** in one of the owner's repos without a Dockerfile, add a simple Dockerfile, push, click Re-check, and capture "Docker: Claimed → Used".

If time is short before the Stage 1 deadline: steps 1–7 minus S4 are the minimum. S4 can be described as Stage 3 in the PPT.

### Screenshots for the Stage 1 PPT
1. S1 with the step tracker mid-run
2. S2 skill grid
3. S2 evidence panel with code snippet + verification ticks (**most important**)
4. S2 "Rejected AI claims" section
5. S2 Growth Plan showing a real upgrade after Re-check
6. S3 recruiter one-minute view
7. S4 placement heatmap

### After Stage 1
- **Stage 2 (online prototype):** deploy the frontend to Vercel and the backend to Render/Railway; env vars on both; a public demo link; one public repo + resume flow must work end to end. Optionally add Groq as a fallback LLM selected via `.env`.
- **Stage 3 (finale):** project documents/portfolio as extra sources, stronger placement-cell features, accuracy report from hand-checked claims, performance and reliability hardening.

---

## 14. Pitch points (for PPT and judges)

- Problem: resumes list skills but don't back them with evidence; AI tools guess and invent (~1 in 5 normalised skills wrong in research).
- What exists fails: ATS checkers, opaque GitHub scores, sandbox tests; none links skills to file-level proof.
- Our answer: verify-by-construction + Evidence Ladder + Growth Loop + defend-your-work kit, with a one-minute recruiter view and a placement-cell dashboard.
- Honest framing: a transparent evidence-retrieval system with checkable trails, not a machine that "proves" skills with certainty. Explainability also aligns with regulations like the EU AI Act, which treats hiring AI as high-risk (talking point, not the main pitch).
- Don't cite any paper on a slide without checking it first.

---

## 15. Known limitations and risks

- Private work is invisible: missing evidence means "no evidence found", not "not skilled".
- Authorship via commit author can be imperfect (squashed merges, mismatched git emails). Show the evidence; let humans judge.
- AI-generated code in a student's repo can't be reliably detected and stays **out of scope**, apart from AI chat leftovers pasted into files (`ai_leftover`); the interview kit is the mitigation.
- Fraud checks are evidence for a human, not verdicts: only an earlier public source (two lines in a file that appeared earlier elsewhere) is excluded automatically, and the student can request a review. Commit timing can be faked by an informed user (dates are set locally), which is one reason the signals are hidden from students.
- GitHub rate limits: always use a token; cache aggressively.
- LLM cost/quota: one call per stage, cache by input hash.
- SQLite is fine for the prototype; revisit only if deployment requires it.

---

## 16. Working rules for the AI coding agent

- Read this file before each task. Read only the code files needed for the current task.
- Keep replies short: files changed + how to run + test results.
- Never rewrite working files to fix an unrelated issue. Fix the specific problem.
- When giving code to the project owner in chat, give exact file paths and full file content to paste, not zip downloads.
- Prefer fewer features that work over many that don't.
- Record any deviation from this file in the Decision log below.

## Decision log
- 2026-10-11: Design finalised as described in this file.
- 2026-10-11: Added advisory **authorship/integrity signals** to each verified code citation (`evidence.integrity`): line-level git blame share (GraphQL, needs token), user's share of the file's commits + who created the file, bulk-upload detection (file created in a ≥20-file or ≥2000-line commit and never edited), repo commit count, and copy detection via GitHub code search (`copy_check.py`, max 8 searches per passport, cached). **Why advisory, not a 6th hard check:** honest students often upload in one commit, and code search matches also catch people who copied *from* the student. Signals are shown in the evidence panel and as "N to check" on the recruiter view; they never change verification, levels or role match. AI-generated code detection remains out of scope.
- 2026-10-11: Added `ai_leftover` signal (AI chat text such as "Here's the updated code" or Markdown fences left in comments; concrete when found, cannot rule AI out when absent). Copy matches only warn when the other repo was **created before** the student's repo (a later repo may have copied from the student). Placement Cell now has a **candidate ranking**: must-have requirements met → all requirements met → verifiability tier (High/Medium/Low/Not checked, from counts of passed advisory checks) → fewer warnings → more code-backed skills; one row per GitHub user (latest passport). No numeric skill scores. Backend uses `truststore` so HTTPS uses the OS certificate store (Python's bundled certs failed on the dev machine).
- 2026-10-13: Bug-fix pass. Config files match by file name at any depth (`backend/Dockerfile` counts). CI/CD recognises GitHub Actions plus Forgejo/Gitea/Travis/GitLab/CircleCI/Jenkins/Azure/Bitbucket. **Git** gets `Used` from `.gitignore` or from commits the user authored in a non-fork repo (new evidence type `commit`, linking to the exact commit; it never makes a skill Applied). One of the 4 source-file slots per repo is reserved for a test file (files named like tests first; a long test file keeps its first 300 lines, so line numbers still match GitHub); an authored test file with a test signature becomes deterministic **code** evidence for Unit Testing (all 5 checks pass), and test files are grouped to one config item per repo (no second citation when the AI already cited that file). Code signatures now match from a word start (`it(` no longer matches `emit(`). Resume terms that are common English words (go, react, express, torch, shell) only count in their capitalised tech form. Re-check reports skills that appear for the first time (`Not found → Used`) and keeps `created_at` (adds `updated_at`). If the growth/interview Gemini call fails, the verified passport is still saved with a visible `generation_warning`. CORS origins come from `FRONTEND_ORIGINS` in `.env`. UI shows real skill names from `GET /skills`, and the Placement Cell shows one row per student.
- 2026-10-15: `signature_found` no longer passes on file extension alone for frameworks/tools/practices; only Language skills are evidenced by extension, everything else needs a real code signature near the cited lines (a plain `add()` in a `.py` file was counted as PyTorch evidence). Docker signatures are now Docker CLI/SDK calls (`FROM ` matched every Python `from x import`) and CI/CD uses `jobs:`/`runs-on:` (`on:` matched ordinary YAML/code).
- 2026-10-15: A **confirmed copy** is excluded, not just warned: when the first code search finds the cited line in another owner's non-fork repo created before the student's repo, a second, different line from the same citation is searched in that repo (`repo:` qualifier, counted in `MAX_SEARCHES` = 8). Both found → the citation moves to `rejected_claims` (`kind: "copied"`, `failed_checks: ["not_copied"]`, `source_url`) and the level is recomputed with `compute_level`; one line only stays advisory. **Why:** two different lines in the same older repo is concrete evidence of copying, while one shared line is often boilerplate. Exclusion runs before role matching. Other rejected claims are tagged `kind: "unsupported"`. Any confirmed copy makes verifiability "Low".
- 2026-10-15: Two advisory repo-level **commit timing** signals from the user's commit list (`user_commits`, one extra call per repo, reused for the `by_user` count): `commit_spread` warns when all of the user's commits fall on one UTC day in a repo with ≥15 source files; `history_import` warns when ≥3 commits and at least half are dated >7 days before GitHub's server-recorded `created_at`. **Why advisory:** honest students sometimes build locally before pushing, so the wording stays neutral and levels never change. GitHub cache files now carry `schema_version: 2`; older files are refetched so new fields always exist.
- 2026-10-15: **"Used" requires authorship.** Ingestion checks every dependency and config file for a commit by the user (`authored_paths`); dependency/config evidence sets `checks.authored` from it, and `compute_level` only counts authored items (missing data = not authored). Unauthored items stay attached with the note "Found in a file you did not commit to; not counted." The grouped Unit Testing test-files item keeps counting (test files aren't authorship-checked). **Why:** a cloned repo's Dockerfile or requirements.txt was enough to make Docker/Flask "Used" without the student writing anything.
- 2026-10-15: **Fraud signals are hidden from students in the API**, not just the UI. `GET /passport/{id}`, `GET /passports` and the re-check response return `student_view()`: no `integrity`, no `good_answer_mentions`, and copied exclusions collapsed into one `{"kind": "review", "count": N}` line with no files or sources. Recruiters use `GET /passport/{id}/review` and `GET /passports/review` (full data), gated by `X-Recruiter-Key` when `RECRUITER_KEY` is set (open in local dev). New recruiter page `/recruiter/:id/evidence` shows every signal and excluded claim. **Why:** showing the checks to the person being checked teaches them how to evade them.
- 2026-10-15: Summary of the fraud-detection pass: confirmed copies (two different lines found in an older repo) are the **only** automatic exclusion; every other signal stays advisory. "Used" requires authorship of the dependency or config file. Fraud signals are hidden from students in the API. Documented in sections 3.2, 8 (P1, P4, P4b), 9, 10 and 15.
- 2026-10-15: **A bad Gemini answer no longer fails the passport.** Claim proposals drop unknown skill ids and citations beyond 3 per skill (`_lenient_proposal`) instead of failing; dropped claims never reach the verifier. Growth/kit output keeps only tasks for real gaps on real repos and kit entries for the supplied skills; it retries only on invalid JSON/schema or when gaps exist but no task is usable, and after the retry returns empty lists plus `generation_error` (stored in the passport; the text never includes model output, so interview answers can't leak to students). All Gemini calls use `temperature=0` so a re-check doesn't change claims by chance. **Why:** one malformed item was failing a 60-second job whose verified evidence was already valid.
- 2026-10-15: Ingestion skips a repo (or records data as unknown) on any GitHub error except rate limits (`GitHubRateLimitError`: 429, or 403 with no remaining quota). Empty repos (409) used to fail the whole job. **Git** evidence is now one `config` item per non-fork repo where the user authored commits (`file_path: "(commit history)"`, link to the user's commits, `checks.authored: true`), so it counts under "Used requires authorship". Test-file evidence is capped at 3 items per repo (`MAX_TEST_FILE_EVIDENCE`), each with only `file_exists`/`not_fork` checks and no line anchor, because no content is fetched; one stored passport had 1,725 items.
- 2026-10-15: **Source file selection** reads up to 6 files per repo (was 4), up to 600 lines (was 300). Files changed in the user's 10 most recent commits come first, then `_candidate_priority`; files with ≥15 lines are preferred; files whose blob size can't fit are skipped before fetching; one slot stays reserved for a test file. The claim prompt is capped at ~8,000 lines, dropping each repo's lowest-ranked files first. GitHub cache `schema_version` is now 3. **Why:** evidence came from toy files in alphabetical order, longer real files were skipped, and Re-check couldn't see code added for a growth task.
- 2026-10-15: Re-check `level_changes` compare the union of old and new skills (after copy exclusion) with a missing skill counted as "Claimed" (was "Not found"), and are computed whenever a previous passport exists (an old passport with no skills used to report nothing). The interview kit uses the strongest skills (Demonstrated, then Applied, then most evidence; was the first 5 alphabetically) and, per skill, the citation whose `line_share` is ok, then the longest range; the prompt asks about design decisions, failure cases and modifying this code, not language trivia. **Why:** the kit should test the candidate's own, strongest work.
- 2026-10-15: Display pass. Passport skills, role-match requirements and `/roles` requirements carry the skill's display `name` (frontend `skillLabel()`). Recruiter "Verified strengths" shows the 5 strongest skills (level, then evidence count). Evidence panel shows only the checks that apply to the evidence type, with ✗ only for an applicable failed check. The student page no longer links to the recruiter view. Resume/certificate mention lines are redacted (I11) when parsed and when stored mentions are reused on re-check; the whole-resume name heuristic is skipped for single lines, where "Python Django" would look like a name. The `go` skill no longer has the alias "go". The frontend tsconfig is now solution-style (`tsconfig.app.json` + `tsconfig.node.json`, both `noEmit`), as in the current Vite template, so `tsc -b` no longer writes `vite.config.js`, which Vite was loading instead of `vite.config.ts`. A nested Dockerfile already counted for Demonstrated (rule matches by file name); a test now covers it.
- 2026-10-15: `backend/scripts/recheck_all.py` rebuilds every stored passport with the same `recheck_passport()` the Re-check endpoint uses (fresh GitHub read, stored mentions), so stored passports pick up rule changes.
- 2026-10-15: **Coverage.** Ingestion records per repo how many source files exist, how many were read, lines read and why the rest weren't (`too_large`, `too_many_lines`, `unreadable`, `empty`, `file_limit`), plus which repos were skipped and why (fork, not public, beyond the 5 most recently pushed, empty/errors). Each passport stores a `coverage` object (also counting files left out of the AI proposal by the ~8,000-line cap), shown as a one-line, expandable summary on the student, recruiter and evidence pages. A missing requirement's reason is "No evidence found in the analysed files (N of M source files read)", and the UI says "Not found" instead of "Missing". Cache `schema_version` 4. **Why:** the system reads a sample, so absent evidence must read as "not found in what we read", never "lacks the skill".
- 2026-10-15: **Earlier public source** (replaces "confirmed copy"). Exclusion means "can't attribute to the candidate", not "copied". File-level first-appearance dates are required; repo creation dates were not reliable (a repo created in 2022 may add the code in 2025, possibly from the candidate). A citation is excluded only when both distinctive lines are in the matching file (checked by reading that file) and that file's oldest commit is more than 1 day before the candidate's file's oldest commit (`first_commit_date`, stored per source file at ingestion; cache `schema_version` 5). Unknown dates (including renames, which path history doesn't follow) stay an advisory warning. Names: signal `earlier_source` (+ `source_repo`, `source_url`, `source_first_seen`, `candidate_first_seen`), label "Earlier public source", rejected `kind: "earlier_source"` (stored `kind: "copied"` is still read as the same), `exclude_earlier_sources()`. Recruiter wording is neutral: "Matches code that appeared in {repo} on {date}, before this file's first commit here ({date}). Not counted as the candidate's own work. This may be legitimate reuse; review if relevant."
- 2026-10-15: **Honest level labels.** Ladder definitions now describe evidence of use (Unverified: mentioned, no evidence in the analysed files; Used: listed in a dependency/config file you committed to; Applied: used in source code you committed to, exact lines cited; Demonstrated: applied and exercised by tests that target that code), with the note "Levels show evidence of use in your code, not how well the skill is used." "Prove/expert/mastery/competence" wording was removed from the UI and our own text; "verified" only describes citations. **Why:** the system shows that a skill was used in sampled code, which says nothing about how well.
- 2026-10-15: **Students see outcomes and can request a review.** Detector internals (advisory signals, thresholds, interview answers) stay hidden, but `student_view()` now lists every excluded citation (skill, repo, file, lines, link to the student's own lines, `source_url`, a neutral reason, `citation_key`) instead of one summary line. New `review_requests` table (`citation_key` = `{skill_id}:{repo}:{file_path}:{start}-{end}`, note ≤1,000 chars, `open`/`reviewed`); `POST /passport/{id}/review-requests` only for excluded citations, ≤10 per passport, one open request per citation; `POST /review-requests/{id}/mark-reviewed` uses the recruiter key; review endpoints include `review_requests`. Requests survive re-check and never change levels or exclusions. **Why:** a decision that affects the student must be visible and contestable, while the detection details stay private. Also fixed: the re-check endpoint shared `recheck_passport`'s name and called itself, so every Re-check returned 502 since the previous session.
- 2026-10-15: **"Demonstrated" is skill-specific.** It needs a test in the same repo that targets one of the skill's cited files, by name or by import (rules in section 3.1); ingestion reads the top of up to 5 test files per repo for import matching (`test_contents`, its own fetch budget, preferring names that share a stem with files read; cache `schema_version` 6). CI workflows and Dockerfiles no longer upgrade every Applied skill in the repo; Docker and CI/CD keep their own evidence. A test file cited for Unit Testing is Applied unless another test targets it. **Why:** one test or Dockerfile anywhere in a repo was upgrading unrelated skills to Demonstrated.
- 2026-10-15: **Evidence record** on every code citation: `commit_sha`, `path`, line range, `snippet_sha256` (SHA-256 of the exact cited lines joined with `\n`), `retrieved_at` (stored per source file at ingestion; cache `schema_version` 7), `verifier_version` (`VERIFIER_VERSION` in `config.py`; bump it whenever verification or level rules change) and `checks` + `rules_applied` (e.g. `applied:code_citation`, `demonstrated:test_name_match`). Shown as a collapsible "Evidence record" on the recruiter evidence page and exported in the badge (`evidenceRecord`); removed from the student view. **Why:** a reviewer can check later that the cited lines and the rules used are exactly what the passport says.
- 2026-10-15: **Reviewer-ready evidence packet** per requirement (`packets.py`, computed on read in the review responses, deterministic, no LLM): status (Met / Partial / Not found) with up to 2 evidence links (code first); **attribution** High / Medium / Low / Not applicable (High: a code citation passes all hard checks and the skill's code evidence has no line-share, file-creator, bulk-upload or earlier-source warning; Medium: some of those warnings but not on every citation; Low: dependency/config only or every code citation warned; Not applicable: not found); short "what's uncertain" lines; and "what to ask next" (the interview-kit question, else a template for Used / Not found / Applied). Recruiter checklist rows stay collapsed (status, attribution, one-line summary) and expand to the four parts; the view still fits 1440×900 collapsed. **Why:** a reviewer needs support, traceability, doubts and a next question per requirement, without a numeric score. Attribution reflects traceability, never ability.
- 2026-10-16: **Citations with no candidate-written lines don't count.** In the same pass that excludes earlier sources (`exclude_uncounted_citations()`, was `exclude_earlier_sources()`), a code citation whose cited lines blame attributes entirely to other GitHub users moves to `rejected_claims` (`kind: "not_written_by_candidate"`, reason "None of the cited lines were last changed in your commits.") and the level is recomputed; under 50% it still counts for Applied but never Demonstrated; unknown blame (missing, or any cited line from an unlinked author) has no effect. The "authored" check is now labelled "You committed to this file" (labels served as `check_labels`), and each code citation carries `lines_written` ({written, cited} or null), shown to students as an outcome. Students see these exclusions and can request a review. **Why:** one comment-only commit made someone else's code count as "Authored by you" (shlok-A-0: 0 of 11 lines).
- 2026-10-16: **Demonstrated needs a test the candidate committed to.** Ingestion records `test_authored` ({path: bool}) for each test file whose content it reads (the existing 5-fetch test budget, plus tests read as source files); `targeting_test(..., committed_only=True)` only accepts those marked True, and a failed lookup leaves the file out, so the skill stays Applied. Cache `schema_version` 8. **Why:** karpathy's `test_engine.py` made shlok-A-0's Python Demonstrated.
- 2026-10-16: **Attribution caps** (`packets.attribution_details`), after the High/Medium/Low base rules: a `repo_history`, `bulk_upload`, `commit_spread`, `history_import` or `ai_leftover` warning, or a one-line copy match, on any of the skill's cited files or their repositories caps attribution at Medium ("Capped at Medium: only 1 commit in this repository." in "What's uncertain"); an earlier-source exclusion on the same skill caps it at Low; fewer than 4 repo commits on every code citation with unknown line authorship gives "Low (limited history)". `repo_history` now stores its commit `total`. **Why:** gaurav21-png showed High despite a one-commit repo and pasted AI chat text.
- 2026-10-16: Gemini calls also retry connection failures (`httpx.TransportError`: RemoteProtocolError, ReadTimeout, ConnectError; `ConnectionError` resets), at most 2 retries with 3 s / 8 s back-off. **Why:** a dropped connection left miguelgrinberg's passport without a growth plan.
- 2026-10-16: Missing parts are labelled, never blank: "Growth plan unavailable for this passport. Use Re-check to try again." (student growth panel, recruiter header, Placement row) when there are gaps or a `generation_error` but no tasks; "Interview questions unavailable."; "Coverage not recorded. Re-check to update." (`frontend/src/passportNotes.ts`). **Why:** empty areas read as "no gaps" or "nothing to ask".
- 2026-10-16: The Placement Cell's "with code evidence" counts only requirements met at Applied or Demonstrated (`frontend/src/requirements.ts`, tested with `npm test`, Node's built-in runner). **Why:** it counted Used (dependency/config-only) requirements as code evidence.
- 2026-10-16: Badge achievements use the readable skill name (`name`) and keep the id in `skillId`. **Why:** the export showed ids like "unit-testing".
- 2026-10-16: Rejected claims list each failed check once, by label (the reason text and a second "Failed checks" line used to repeat it). **Why:** duplicated text on every rejected claim.
- 2026-10-16: `VERIFIER_VERSION` is now 2026-10-16 (line-share, Demonstrated and attribution rules changed) and each passport stores the `verifier_version` it was built with. `recheck_all.py --only-incomplete` rebuilds only passports missing coverage, missing a growth plan after a generation error, or built with an older version; its "excluded" count covers every excluded kind. **Why:** rebuilding everything wastes the Gemini quota (the free tier allows 20 requests a day).
- 2026-10-19: **Robust Verification Pipeline & Platform Hardening:**
  1. **Persistent SQLite Job Queue & Crash Recovery:** Replaced in-memory dictionary with SQLite persistent jobs table (`storage.py`) with WAL mode and `_recover_running_jobs()` to gracefully transition interrupted jobs on server restart.
  2. **Lean Frontend Architecture:** Removed unused TailwindCSS, PostCSS, and Autoprefixer dependencies. Zero build-step pure CSS styling, non-blocking asynchronous `loadSkillNames()` startup.
  3. **Python AST Verification Engine (`ast_verifier.py`):** Added syntax-aware AST parsing for Python citations (`python_ast_has_signature`). Ensures framework/tool signatures bind to executable AST nodes (`Import`, `Call`, `Decorator`, `ClassDef`, `Attribute`) and strictly rejects signatures appearing solely within comments or string literals.
  4. **Empty / Dummy Test Defense:** Added assertion and test definition validation (`check_test_has_assertions`) to `targeting_test(..., committed_only=True)` so dummy/empty test files lacking assertions or test functions cannot elevate a skill to "Demonstrated".
  5. **Cryptographic Commit Verification Signal:** Added `_commit_signature_signal` to `integrity_signals()` in `verifier.py` to evaluate GPG/SSH commit signature validity in recruiter review.
  6. **Fail-Closed Access Control:** Added `APP_ENV` and `REQUIRE_RECRUITER_AUTH` configuration in `config.py` and `main.py`. In production or when enabled, recruiter review endpoints strictly fail-closed (500 if `RECRUITER_KEY` is unconfigured, 401 if missing or invalid), preventing accidental exposure of sensitive fraud signals.
  7. **GitHub Check Runs CI Status Verification:** Added `_fetch_ci_status()` in `github_ingest.py` (cache schema version 9) and `ci_run_verified` check in `verifier.py`. Rather than merely asserting YAML configuration existence, CI/CD evidence now actively validates whether GitHub Actions had a verified successful run on that commit.
- 2026-10-20: **Audit follow-up:** Re-check now runs as a leased background job and preserves ownership credentials; legacy passports without stored resume/certificate mentions reconstruct safe skill-name mentions. Test-file Unit Testing evidence counts toward Used only when `test_authored` confirms the student committed to the file; authored test files are prioritized before the evidence cap, and `VERIFIER_VERSION` advances to 2026-10-20. This supersedes the 2026-10-15 exception. Corrupt JSON caches are discarded and refetched, polling performs lease-expiry recovery without taking a write lock on every read, resume name-label redaction handles `Full Name`/`Candidate Name` and comma-separated names while avoiding common job-title headers, forwarded client IPs are trusted only through configured proxy addresses after IP normalization, and placement links open reviewer pages. The mobile navigation keeps Placement Cell visible, and badge downloads delay blob-URL cleanup. **Why:** prevent lost claims, false authorship, avoidable database contention, spoofed rate limits, accidental name exposure, and broken mobile/download flows.
- 2026-10-20: Re-check retains previously recorded skills that fresh verification no longer supports as `Claimed`, with no stale evidence or certificate marker; role matching sees the retained claimed state. `VERIFIER_VERSION` advances to 2026-10-20.1 so `recheck_all.py --only-incomplete` selects passports built under the prior re-check behavior. This cannot reconstruct skill history already removed from a passport unless another saved mention or fresh source supports it.
- 2026-10-20: **Authorization, persistence and recovery hardening:** Review requests require the passport owner token; SQLite serializes limit/duplicate validation and enforces one open request per citation, migrating old duplicates to reviewed history before creating the partial unique index. Passport owner hashes are stored independently from short-lived job rows; 30-day job cleanup and hourly rate-limit cleanup run at startup, with expired rate-limit entries also removed during rate-limit checks. Active jobs use renewable 120-second leases with a 30-second heartbeat so another worker does not fail a slow pipeline. Student responses omit stored resume/certificate mentions, and parser mentions contain only the matched skill name. The frontend restores and retries pending jobs from `sessionStorage`; review actions are shown only in the owner browser session. The app defaults to production auth and requires an explicit local-development setting. An ASGI request-body cap runs before multipart parsing, and CI runs backend tests plus frontend tests/build on pushes and pull requests.
- 2026-10-19: **Identity checks** (`identity.py`). Non-demo passports need an account-ownership proof: a one-time code published as a public gist from the GitHub account, checked before the job starts (403 otherwise; `REQUIRE_OWNERSHIP_PROOF`). Each passport stores `identity`: `account_ownership` and the advisory `resume_github_link` (resume links to the same account). Also a per-repo advisory `backdated_commits` signal (committer date much later than author date). **Why:** every other check proves code was written by a GitHub account, not that the applicant controls it; submitting a well-known developer's username was the biggest fake-candidate hole. A gist check needs no OAuth app. Identity checks are hidden from students (`student_view` drops `identity`), like the other signals.
- 2026-10-19: **CI runs are context, not a check.** `ci_run_verified` is no longer added to `checks`; CI workflow evidence carries `ci_run: {verified, status: passed|failed|in_progress|none}` instead (the label stays in `CHECK_LABELS` for older stored passports). **Why:** a beginner's workflow that never ran or failed still shows they wrote it, so a run's outcome must not make valid evidence look like it failed a check.
- 2026-10-19: `VERIFIER_VERSION` is now 2026-10-19 (identity checks, `backdated_commits`, advisory CI runs). Removed `verification_pipeline_plan.md` (internal audit notes with local paths and unverified figures).
- 2026-10-20: **Copy exclusion applies per file.** When a citation is excluded as an earlier public source (the confirmed two-line match), `propagate_earlier_sources_per_file()` (called at the start of `exclude_uncounted_citations()`) gives every other code citation of that repo + file the same `copy_match` signal, so it is excluded with the same reason, source link and student wording, and levels are recomputed. One-line advisory matches don't spread; no extra GitHub calls. `VERIFIER_VERSION` is now 2026-10-20. **Why:** boilerplate citations (imports, app setup) can't be searched, so they passed even when the same file was proven copied (jobs-scraper-api/app.py: lines 58-78 excluded, lines 7-9 still made Flask Applied).
- 2026-10-20: **Ownership proof is bound to one passport; production can't switch it off.** `verify_challenge()` records the passport (creation job id) in the same single-use update that consumes the code, and `ownership_signal()` is ok only for that passport's own proof (`ownership_for_passport()`); demo passports are always unknown. `startup()` refuses `REQUIRE_OWNERSHIP_PROOF=false` unless `APP_ENV=development`, and refuses unrecognised values, which keep the gate on. **Why:** the signal looked up any earlier proof for the username, so a demo passport, or one created with the gate off, showed "ok" after the real owner had proved once; and an unchecked setting could silently disable the gate in production. Passports built before this change have no recorded link, so a Re-check shows them as created without a proof.
- 2026-10-20: `VERIFIER_VERSION` is now `2026-10-20.1` (sorts after `2026-10-20` as text; `recheck_all.py` compares versions as strings). **Why:** the audit follow-up on `main` and this branch's per-file copy exclusion and passport-bound ownership both used `2026-10-20`, so `recheck_all.py --only-incomplete` would skip passports built earlier today and keep stale copy results and a stored ownership "ok" without a linked proof. After deploying, run it deliberately: each rebuild is a fresh GitHub read plus about 2 Gemini calls.
- 2026-10-20: `VERIFIER_VERSION` is now `2026-10-20.2`. **Why:** PR #4 (retain historical skills on Re-check) also moved `main` to `2026-10-20.1`, so passports rebuilt under it would otherwise not be selected for the per-file copy exclusion and passport-bound ownership.
- 2026-10-21: GitHub ingestion reads at most 12 dependency/configuration files per repository (small, shallow paths first), skips files over 256 KB, and reports omitted files in coverage. Optional authorship/history/CI and recency lookups treat transport failures as unknown, while rate limits still stop the job. Bumped the ingestion cache schema to 10 and `VERIFIER_VERSION` to `2026-10-21` because sampling can change available verification evidence. **Why:** bound per-passport API work on monorepos without hiding that some evidence was skipped, and avoid turning transient failures in advisory lookups into a full generation failure.
- 2026-10-21: **Certificate images and navigation UX.** Text-based certificate PDFs continue to be parsed locally; scanned PDF pages and PNG/JPEG certificate images are OCRed with Google Cloud Vision using backend Application Default Credentials, with a 10-page OCR cap per certificate. The UI discloses when scanned certificate content is sent to Vision. Generation polling now lives above the route, persists the latest step, and shows a cross-page ready/status notice. Student and recruiter passport pages discard stale responses when the route id changes. Placement Cell separates the role used to evaluate everyone from a filter on the students' saved preferred role; student passports link through with their preference preselected. Also added a loading/retry state for cohort fetches, visible upload focus, and a not-found page. **Why:** support scanned credentials without confusing them with text PDFs, keep users informed while work continues, and make cohort filters reflect candidate preferences without silently changing evaluation criteria.
- 2026-10-21: **Custom roles from a job description** (section 7.6). AI proposes role requirements and importance; code matches aliases, sets minimum levels by fixed rules and checks every quote against the description. Shared quote/skill validation lives in `claim_validation.py`; the "propose a JSON list" Gemini call (`propose_json_items()` in `gemini.py`) is cached by prompt and retries once on invalid JSON. The description is not stored; the role definition is stored in `custom_roles` and on the passport (`custom_role`) so Re-check reuses it. `ENABLE_CUSTOM_ROLES` (default true) hides it. No verification, level or role-matching rule changed, so `VERIFIER_VERSION` is unchanged. **Why:** students target real postings, not only the 4 sample roles, and an AI-read requirement must be as checkable as an AI-proposed citation.
- 2026-10-21: **Project reports and a portfolio link** (section 7.7). Reports are claims: AI proposes, code validates quotes and cross-checks each claim against code evidence; reports never raise levels. Portfolios: ownership only for <username>.github.io; external sites are never fetched. Not-evidenced report claims become unverified mentions; links to another account's repositories are recruiter-only. `ENABLE_REPORTS` (default true). `VERIFIER_VERSION` unchanged (no verification or level rule changed). **Why:** the brief names project documents and portfolios as sources, but a report can say anything, so it may only point at skills the code must then show.
- 2026-10-22: Exact-match job-description quotes stop at sentence ends (`. ; ! ?`) and line breaks (`_quote_around()` in `jd_parse.py`). **Why:** a ±60-character window crossed sentences, so "Write unit tests with pytest" leaked into the FastAPI and SQL quotes and the testing-word rule raised both minimums to Demonstrated. Role-matching and level rules are unchanged.
- 2026-10-22: **Job descriptions fall back to exact matching** when Gemini is unavailable after its retries (was a 503). The result uses only deterministic matches and the existing keyword/level rules and is flagged `"mode": "exact_only"` with a visible note; no must-have is still a 422. **Why:** `gemini-flash-latest` returned 503 "high demand" for long stretches, which blocked custom roles entirely although the exact path needs no AI.
