# CLAUDE.md: Skill Passport (Proof, Not Claims)

This file is the single source of truth for this project. Read it fully before writing any code. Every decision below was made deliberately after research; do not change a decision without a clear reason, and if you do, record it in the "Decision log" at the bottom.

---

## 0. TL;DR for the developer

We are building **Skill Passport**, an AI system that turns a person's real work (GitHub repos, resume, certificates) into a **verified skill profile** where **every skill is linked to the exact file and lines that prove it**. An LLM only *proposes* skills with citations; **plain deterministic code verifies** every citation. Skills without evidence are shown as "Unverified" and never counted. The profile is compared requirement-by-requirement with a target role, each gap becomes a **task on the student's own repo**, and re-checking the repo after the task **upgrades the skill automatically**.

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
- **Resume-vs-GitHub verifiers on GitHub (SkillGuard AI, CodeCred AI, Resume Claim Verifier):** check repo names, languages, topics, READMEs, or dependency files. A README can say anything; a dependency proves an import, not skill. Evidence is repo-level, not file/line-level. Some use "AI checks AI" critic agents instead of hard rules.
- **Career/gap tools (Candid, Jobscan-style ATS checkers):** good gap analysis and roadmaps, but never verify that skills are real; roadmaps are generic and not tied to the student's own code.
- **SkillProof AI (github.com/hxrrrrri/Skillbuilder_AI):** the closest competitor, likely built for a similar hackathon. Evidence-backed rubric scores, ownership tokens, validators that can only lower scores, publish gates. Weaknesses: scores general qualities (code quality, testing), not specific skills; no learning plan; GitHub only; very heavy and complex. **Do not copy its design.** Judges may know it.
- **Enterprise platforms (Eightfold, TechWolf, Beamery, Gloat):** use private HR data, black-box inference, not available to students.
- **Assessment platforms (HackerRank, CodeSignal, Workera):** prove skill in a test sandbox, not through real work. Remote assessments are increasingly broken by AI cheating (CodeSignal reported fraud attempts rising from 16% in 2024 to 35% in 2025).
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
| **Claimed** (shown as "Unverified") | Appears only on the resume or a certificate; no code evidence. | Grey `#8A8F9C` |
| **Used** | Appears only in a dependency or config file **the student committed to** (e.g. `flask` in their requirements.txt, their Dockerfile), or in their commit history (Git). Files they never touched stay attached but don't count. | Amber `#D99A00` |
| **Applied** | At least one verified citation in source code the student authored. | Green `#2FB36B` |
| **Demonstrated** | Applied AND the same repo has tests, CI (`.github/workflows/`), or a Dockerfile/deploy config. | Dark green `#1E7A46` |

Certificates add a "Certified" tag to a skill but never raise its level on their own.

### 3.2 Fraud and authorship checks
Only two things change what counts; everything else is an advisory signal for a human.

| Check | Effect | Rule |
|---|---|---|
| Signature check (`signature_found`) | Hard check (claim rejected) | Language skills are proven by file extension. Frameworks, tools and practices need a real code signature within ±5 lines of the citation (whole-word start, case-insensitive). |
| Confirmed copy | Citation **excluded** (`kind: "copied"`) | A distinctive cited line is found in another owner's non-fork repo created **before** the student's repo, **and** a second, different line from the same citation is found in that same repo. Level recomputed. |
| Authorship of dependency/config files | Counts towards Used only if authored | `authored_paths` from ingestion; missing = not authored. |
| `line_share`, `commit_share`, `bulk_upload`, `repo_history`, `ai_leftover` | Advisory | Per file (see Decision log, 2026-10-11). |
| `copy_match` (one line only) | Advisory | One shared line is often boilerplate or a tutorial. |
| `commit_spread` | Advisory, per repo | Warn when all the user's commits fall on one UTC day in a repo with ≥15 source files. |
| `history_import` | Advisory, per repo | Warn when ≥3 commits and at least half are dated >7 days before GitHub's `created_at`. Neutral wording: honest students sometimes build locally first. |

Fraud signals are **never** returned by student endpoints (see section 9). A confirmed copy makes verifiability "Low".

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
9. Fraud signals (integrity signals, interview-kit answers, copy sources) are never returned by student-facing endpoints.

---

## 5. Tech stack (decided, don't change mid-build)

- **Backend:** Python 3.11, FastAPI, httpx, pdfplumber (layout-aware PDF parsing; handles two-column resumes), google-genai (Gemini), pydantic, SQLite.
- **Frontend:** React + Vite + TypeScript + Tailwind CSS + react-router + recharts.
- **LLM:** Gemini Flash (current model, name set via `GEMINI_MODEL` in `.env`). Optional later: Groq as a fallback provider.
- **External APIs:** GitHub REST API with a personal access token (classic, read-only public repos). Unauthenticated limit is only 60 requests/hour.
- **Ports:** backend 8000, frontend 5173 (CORS enabled for 5173).
- **Environment (`backend/.env`):** `GITHUB_TOKEN`, `GEMINI_API_KEY`, `GEMINI_MODEL`.
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
- `code_signatures` (strings/regex proving usage in source, e.g. `"from flask import"`, `"@app.route"`, `"import React"`, `"SELECT "`)
- `config_files` (presence proves usage, e.g. `"Dockerfile"`, `".github/workflows/"`, `"docker-compose.yml"`)

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
- pdfplumber with layout-aware extraction.
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
5. `signature_found`: for Language skills, the file extension matches; for every other skill, the file extension is allowed (if the skill lists any) **and** a code_signature appears within the cited lines (±5 line window). Extension alone never proves a framework.

All 5 pass = verified; otherwise move it to `rejected_claims` with reasons (`kind: "unsupported"`).
Then add deterministic evidence the LLM may have missed: dependency matches → Used; config files (Dockerfile → Docker, workflows → CI/CD, tests → Unit Testing). Dependency/config evidence sets `checks.authored` from `authored_paths`.
Compute ladder levels per section 3.1 with `compute_level()` (only authored dependency/config evidence counts).
Each verified code citation gets advisory integrity signals (section 3.2), including the repo-level `commit_spread` and `history_import`.

**P4b. Copy check** (`copy_check.py`, then `exclude_confirmed_copies()` in `verifier.py`)
- Order: verify → `add_copy_signals` → `exclude_confirmed_copies` → role match → growth/kit, so role matching sees post-exclusion levels.
- Code search for the longest distinctive cited line; if another owner's older non-fork repo has it, search a second, different line with `repo:<owner>/<name>`. Both found = confirmed copy (`confirmed_copy`, `source_repo`, `source_url`, `source_created_at`). Max 8 searches per passport, second searches included.
- Confirmed copies are removed from the skill, added to `rejected_claims` (`kind: "copied"`, `failed_checks: ["not_copied"]`, `source_url`), and the level is recomputed; a skill left with no evidence is removed unless the resume or a certificate mentions it (then Claimed).

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

**Re-check (Growth Loop):** re-run P1 bypassing the cache, then P4–P6; return a list of level changes (e.g. `"docker": "Claimed → Used"`).

---

## 9. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/roles` | Role list |
| POST | `/passport/create` | multipart: `github_username`, `resume` (PDF), `certificates[]` (optional), `target_role` → returns `job_id` |
| GET | `/jobs/{id}` | Step status: Reading GitHub → Reading resume → Proposing skills → Verifying evidence → Matching role → Building growth plan → Done |
| GET | `/passport/{id}` | Student-safe passport (`student_view`: no integrity signals, no good-answer notes, copied exclusions summarised as one `kind: "review"` line) |
| GET | `/passports` | All passports, student-safe |
| GET | `/passport/{id}/review` | Full passport for recruiters (needs `X-Recruiter-Key` when `RECRUITER_KEY` is set) |
| GET | `/passports/review` | All full passports (Placement Cell); same key rule |
| POST | `/passport/{id}/recheck` | Growth Loop re-verification; returns the student-safe passport and level changes |
| GET | `/passport/{id}/badge` | Open Badges JSON-LD |
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
- Skill side panel: each evidence item (repo / file / lines, code snippet with line numbers, commit SHA, "Open on GitHub"), verification checklist ticks (File exists, Lines valid, Authored by you, Not a fork, Skill found in code).
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
Role selector; student count; average requirements met; heatmap (students × required skills, coloured by level, grey = missing); recharts bar chart of % of students missing each skill; "Suggested workshop: <top gap>"; links to each passport and recruiter view.

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

- Problem: resumes list skills but don't prove them; AI tools guess and invent (~1 in 5 normalised skills wrong in research).
- What exists fails: ATS checkers, opaque GitHub scores, sandbox tests; none links skills to file-level proof.
- Our answer: verify-by-construction + Evidence Ladder + Growth Loop + defend-your-work kit, with a one-minute recruiter view and a placement-cell dashboard.
- Honest framing: a transparent evidence-retrieval system with checkable trails, not a machine that "proves" skills with certainty. Explainability also aligns with regulations like the EU AI Act, which treats hiring AI as high-risk (talking point, not the main pitch).
- Don't cite any paper on a slide without checking it first.

---

## 15. Known limitations and risks

- Private work is invisible: missing evidence means "no evidence found", not "not skilled".
- Authorship via commit author can be imperfect (squashed merges, mismatched git emails). Show the evidence; let humans judge.
- AI-generated code in a student's repo can't be reliably detected and stays **out of scope**, apart from AI chat leftovers pasted into files (`ai_leftover`); the interview kit is the mitigation.
- Fraud checks are evidence for a human, not verdicts: only a confirmed copy (two lines in an older repo) is excluded automatically. Commit timing can be faked by an informed user (dates are set locally), which is one reason the signals are hidden from students.
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
- 2026-10-15: `signature_found` no longer passes on file extension alone for frameworks/tools/practices; only Language skills are proven by extension, everything else needs a real code signature near the cited lines (a plain `add()` in a `.py` file was "proving" PyTorch). Docker signatures are now Docker CLI/SDK calls (`FROM ` matched every Python `from x import`) and CI/CD uses `jobs:`/`runs-on:` (`on:` matched ordinary YAML/code).
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
