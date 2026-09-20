from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from app.db import queries as q
from app.deps import get_db

router = APIRouter()


@router.post("/jobs/{job_id}/cv/versions/{version_id}/revert")
def revert_tailored_version(job_id: int, version_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    version = q.get_version(conn, "tailored", job_id, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    q.revert_job_cv_version(conn, job_id, version_id)
    q.add_job_event(conn, job_id, "cv", f"Reverted to version {version['hash']}")
    return RedirectResponse(f"/jobs/{job_id}/cv/preview", status_code=303)


@router.post("/cv/{base_cv_id}/versions/{version_id}/revert")
def revert_base_version(base_cv_id: int, version_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_version(conn, "base", base_cv_id, version_id) is None:
        raise HTTPException(status_code=404, detail="Version not found")
    q.revert_base_cv_version(conn, base_cv_id, version_id)
    return RedirectResponse(f"/cv/{base_cv_id}", status_code=303)


@router.post("/jobs/{job_id}/cv/versions/{version_id}/accept")
def accept_tailored_version(job_id: int, version_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    version = q.get_version(conn, "tailored", job_id, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    q.accept_job_cv_version(conn, job_id, version_id)
    q.add_job_event(conn, job_id, "cv", f"Version {version['hash']} accepted")
    return RedirectResponse(f"/jobs/{job_id}/cv/preview?version={version_id}", status_code=303)


@router.post("/cv/{base_cv_id}/versions/{version_id}/accept")
def accept_base_version(base_cv_id: int, version_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_version(conn, "base", base_cv_id, version_id) is None:
        raise HTTPException(status_code=404, detail="Version not found")
    q.accept_base_cv_version(conn, base_cv_id, version_id)
    return RedirectResponse(f"/cv/{base_cv_id}?version={version_id}", status_code=303)


@router.post("/cv/{base_cv_id}/accept")
def accept_base(base_cv_id: int, conn: sqlite3.Connection = Depends(get_db)):
    q.accept_base_cv(conn, base_cv_id)
    return RedirectResponse(f"/cv/{base_cv_id}", status_code=303)
