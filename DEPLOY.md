# Deploying Skill Passport

Backend: **Render** (FastAPI + SQLite on a 1 GB persistent disk, from `render.yaml`).
Frontend: **Vercel** (static Vite build, from `frontend/`).

Do the steps in this order: **1 Render → 2 Vercel → 3 connect them (CORS) → 4 copy data → 5 checklist.**

Deploy from `main` after the `feat/deploy` PR is merged. To try it first, pick `feat/deploy` as the branch in steps 1 and 2 and switch to `main` later.

---

## 0. Before you start

You need:
- A Render account with a payment method. Persistent disks only work on paid instances; the Blueprint uses `0.5c-512mb`.
- A Vercel account connected to the GitHub repo.
- Your values for `GITHUB_TOKEN` and `GEMINI_API_KEY` (from your local `backend/.env`).

Generate the recruiter secret (PowerShell, from the repo root):

```powershell
backend\.venv\Scripts\python.exe -c "import json,secrets; print(json.dumps({'local-recruiter': secrets.token_urlsafe(32)}))"
```

It prints something like `{"local-recruiter": "Xq3...43 characters..."}`. That whole line is the value of `RECRUITER_ACCOUNTS`.
- Keep the name `local-recruiter`. The 8 interview sets in your local database belong to that recruiter, and interview sets are visible only to the recruiter who created them.
- For more recruiters, add more names: `{"local-recruiter": "...", "reviewer-asha": "..."}`. Each secret must be at least 32 characters, and secrets must be different from each other.
- Store the secret in a password manager. The secret is the recruiter's **password** on the sign-in screen.

---

## 1. Render (backend)

1. Render Dashboard → **New +** → **Blueprint**.
2. Connect the GitHub repo `shlok-A-0/skill-passport` and choose the branch (`main`, or `feat/deploy` to test). Render finds `render.yaml` at the repo root.
3. Render lists one web service, **skill-passport-api**, with the disk **skill-passport-data** (1 GB at `/var/data`). Fill in the variables it asks for:

   | Variable | Value |
   |---|---|
   | `GITHUB_TOKEN` | your GitHub token (classic, public repo read) |
   | `GEMINI_API_KEY` | your Gemini key |
   | `GEMINI_MODEL` | same as local `.env` (e.g. `gemini-flash-latest`) |
   | `GEMINI_JD_MODEL` | same as local `.env` (e.g. `gemini-3.5-flash-lite`) |
   | `RECRUITER_ACCOUNTS` | the JSON line from step 0 |
   | `FRONTEND_ORIGINS` | `http://localhost:5173` for now (replaced in step 3) |
   | `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `DEEPSEEK_BASE_URL` | optional; leave blank unless you use the DeepSeek fallback |
   | `TRUSTED_PROXY_IPS` | leave blank |

   These are set by the Blueprint and need no input: `APP_ENV=production`, `DATA_DIR=/var/data`, `PYTHON_VERSION=3.12.8`, `REQUIRE_OWNERSHIP_PROOF=true`, `ALLOW_DEBUG_ENDPOINTS=false`, and the four `ENABLE_*` flags.
4. Click **Apply**. The first build takes a few minutes.
5. When the service shows **Live**, copy its URL from the top of the service page, e.g. `https://skill-passport-api.onrender.com`.
6. Open `<that URL>/health`. It should show `{"status":"ok","service":"skill-passport"}`.

If the deploy fails on startup, check **Logs**:
- `RECRUITER_ACCOUNTS must be a JSON object...`: the value isn't valid JSON, or a secret is shorter than 32 characters.
- `Missing required production setting(s)`: `GITHUB_TOKEN` or `GEMINI_API_KEY` is empty.

Notes:
- **Region** is `singapore` (closest to India). It can't be changed after creation, so edit `render.yaml` before step 1 if you want another region.
- **Plan:** if Render rejects `0.5c-512mb`, pick any paid instance type in the Blueprint screen. Free instances can't have a disk.
- **Brief downtime on deploys:** with a disk, every deploy stops the old instance before starting the new one, so the API is down for a few seconds.

---

## 2. Vercel (frontend)

1. Vercel Dashboard → **Add New…** → **Project** → import `shlok-A-0/skill-passport`.
2. **Root Directory:** click **Edit** and choose `frontend`.
3. **Framework Preset:** Vite (detected automatically).
4. **Build Command:** `npm run build`. **Output Directory:** `dist`. **Install Command:** default (`npm install`).
5. **Environment Variables:** add `VITE_API_BASE_URL` with the Render URL from step 1, **without a trailing slash** (e.g. `https://skill-passport-api.onrender.com`).
6. Click **Deploy**. When it finishes, copy the production URL, e.g. `https://skill-passport.vercel.app`.

`frontend/vercel.json` sends every route to `index.html`, so reloading `/passport/<id>` or `/placement` works.

`VITE_API_BASE_URL` is built into the bundle. If you change it later, redeploy: Deployments → ⋯ → **Redeploy**.

---

## 3. Connect them (CORS)

1. Render → **skill-passport-api** → **Environment** → edit `FRONTEND_ORIGINS`.
2. Set it to the Vercel production URL, exactly as the browser shows it, with no trailing slash: `https://skill-passport.vercel.app`.
   - To allow more than one origin (e.g. a custom domain), separate them with commas: `https://skill-passport.vercel.app,https://passport.example.com`.
   - Vercel preview URLs (`...-git-branch-....vercel.app`) are different origins. Add one only if you need to test a preview against this backend.
3. **Save Changes**. Render redeploys the service.

---

## 4. Copy your local data to the Render disk (one time)

This uses Render SSH with `scp`. It copies all 15 passports, the interview sets, custom roles and review requests.

**4a. Make a clean copy of the local database.** Run in PowerShell from the repo root. It works while the local backend is running, because SQLite's backup API includes the WAL contents. It also removes local recruiter sign-in sessions so they can't carry over.

```powershell
cd backend
.\.venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect('skill_passport.db'); d=sqlite3.connect('upload.db'); s.backup(d); d.execute('DELETE FROM recruiter_sessions'); d.commit(); d.close(); s.close(); print('ok')"
cd ..
```

**4b. Add an SSH key to Render** (skip if you already have one):

```powershell
ssh-keygen -t ed25519
Get-Content $HOME\.ssh\id_ed25519.pub
```

Render → **Account Settings** → **SSH Public Keys** → **+ Add SSH Public Key** → paste the printed line.

**4c. Get the SSH address.** Render → **skill-passport-api** → **Connect** (top right) → **SSH** tab. Copy the command. It has the form `ssh <service-id>@ssh.<region>.render.com`, and the `<service-id>@ssh.<region>.render.com` part is what you need.

**4d. Upload.** `-s` uses SFTP, as Render recommends. Replace the address with yours:

```powershell
scp -s backend\upload.db srv-XXXXXXXX@ssh.singapore.render.com:/var/data/upload.db
```

**4e. Swap it in.** Render → **skill-passport-api** → **Shell**, then run:

```bash
cd /var/data
cp skill_passport.db skill_passport.before-restore.db 2>/dev/null || true
mv upload.db skill_passport.db
rm -f skill_passport.db-wal skill_passport.db-shm
```

Then **Manual Deploy** (top right) → **Restart service**. The app opens the copied database on restart and adds any tables it needs.

**4f. Delete the local copy:** `Remove-Item backend\upload.db`. It contains every passport, so it shouldn't stay around.

After the copy:
- Passports created before the move can't use owner-only actions such as review requests. Those need the owner token stored in the browser that created them, and that browser was on `localhost`.
- If **Shell** or **Connect → SSH** isn't available, the service is on a free instance. Disks and shell access both need a paid instance.
- The same steps in reverse back up the live database: `scp -s <address>:/var/data/skill_passport.db .`. Render also takes daily disk snapshots, kept for 7 days.

---

## 5. Post-deploy checklist

- [ ] `https://<render-url>/health` returns `{"status":"ok",...}`, and `/ready` returns OK.
- [ ] The Vercel site loads with no errors in the browser console. In particular, no CORS error: if you see one, recheck `FRONTEND_ORIGINS`.
- [ ] Reloading a deep link (e.g. `/placement`) loads the page, not a 404.
- [ ] **Recruiter sign-in:** use an email containing `recruiter` (e.g. `recruiter@techcorp.com`) and the **secret from `RECRUITER_ACCOUNTS`** as the password. The one-click demo recruiter button sends the local password `recruiter2026` and will be rejected in production. That is expected.
- [ ] The Placement Cell lists the copied passports, and a recruiter view opens with its evidence.
- [ ] Interview sets from your local machine are visible when signed in as `local-recruiter`.
- [ ] **Student flow end to end:** create a passport with a public GitHub account, prove ownership with the gist code, upload a **text** resume PDF, and wait for **Done**.
- [ ] Render → **Manual Deploy → Restart service**, then reload: the passports are still there (the disk works).
- [ ] Student pages show no integrity signals or interview answers (sign out and open a student passport).

---

## Known limits on this setup

- **Scanned documents:** OCR uses the Tesseract program, which Render's Python runtime doesn't include. On this deployment, scanned certificates and reports (PNG/JPEG or image-only PDFs) make the passport job fail with "Local Tesseract OCR is unavailable". Text PDFs work normally. Supporting scans needs a Docker-based Render service that installs `tesseract-ocr`. Google Cloud Vision is no longer used, so no credentials file is needed.
- **Rate limits are shared.** Render's proxy addresses aren't fixed, so `TRUSTED_PROXY_IPS` is left blank. The per-IP limits then apply to all visitors together: 20 passport creations, 10 job-description extractions and 10 recruiter sign-ins per 10 minutes. This is fine for a demo, but expect 429 errors if many people try it at once.
- **One instance only:** SQLite on a single disk. Don't raise `numInstances`.
- **Gemini quota:** each passport uses about 2 Gemini calls. The free tier allows about 20 requests a day.
