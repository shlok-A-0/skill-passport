from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile

from app import config
from app.services.github_ingest import GitHubIngestError, ingest_github
from app.services.resume_parse import parse_resume

router = APIRouter(prefix="/debug", tags=["debug"])


def require_debug_access(x_recruiter_key: str | None = Header(default=None)) -> None:
    if not getattr(config, "ALLOW_DEBUG_ENDPOINTS", True):
        raise HTTPException(status_code=404, detail="Debug endpoints are disabled.")
    if getattr(config, "REQUIRE_RECRUITER_AUTH", False):
        # Debug endpoints must be protected when authentication is enforced
        from app.main import require_recruiter
        require_recruiter(x_recruiter_key)


@router.get("/github/{username}", dependencies=[Depends(require_debug_access)])
def debug_github(username: str, refresh: bool = False) -> dict:
    try:
        result = ingest_github(username, force_refresh=refresh)
    except (GitHubIngestError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "username": result["username"],
        "repo_count": result["repo_count"],
        "repos": [
            {
                "name": repo["name"],
                "is_fork": repo["is_fork"],
                "tree_file_count": len(repo["tree"]),
                "source_file_count": len(repo["source_files"]),
                "authored_file_count": sum(
                    bool(file["authored_shas"]) for file in repo["source_files"].values()
                ),
                "dependency_files": repo["dependency_files"],
                "config_files": repo["config_files"],
                "test_file_count": len(repo["test_files"]),
            }
            for repo in result["repos"]
        ],
    }


@router.post("/resume", dependencies=[Depends(require_debug_access)])
async def debug_resume(resume: UploadFile = File(...)) -> dict:
    if resume.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Resume must be a PDF file.")
    content = await resume.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Resume PDF must be 10 MB or smaller.")
    try:
        result = parse_resume(content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "page_count": result["page_count"],
        "skills": result["skills"],
        "redacted_text": result["redacted_text"],
    }
