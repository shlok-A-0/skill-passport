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

`backend/.env` must contain `GITHUB_TOKEN`, `GEMINI_API_KEY`, and `GEMINI_MODEL`. Optional: `FRONTEND_ORIGINS` (comma-separated) lists the frontend URLs allowed to call the API; it defaults to `http://localhost:5173`, so add your Vercel URL when deploying. The existing `.env` is ignored by Git; do not replace it with `.env.example`. The current Gemini model setting is `gemini-flash-latest`.

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
- Placement Cell: <http://localhost:5173/placement>
- FastAPI docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

Three fictional resume PDFs for local demonstrations are in `samples/`. Each includes a visible `DEMO DATA` label. Mark generated passports as demo data in the create form.

## Tests

From the project root:

```powershell
python -m pytest backend/tests -q
cd frontend
npm run build
```
