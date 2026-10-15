from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.github_ingest import GitHubIngestError, ingest_github
from app.services.resume_parse import parse_resume

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/github/{username}")
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


@router.post("/resume")
async def debug_resume(resume: UploadFile = File(...)) -> dict:
    if resume.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Resume must be a PDF file.")
    try:
        result = parse_resume(await resume.read())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "page_count": result["page_count"],
        "skills": result["skills"],
        "redacted_text": result["redacted_text"],
    }
