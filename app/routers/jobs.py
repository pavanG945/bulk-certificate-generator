import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database import get_db, get_session_factory
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import CertificateOut, JobCreate, JobCreated, JobStatusOut
from app.services import jobs as job_service

router = APIRouter(prefix="/api", tags=["certificates"])


def get_certificate_dir() -> Path:
    return settings.certificate_dir


def _get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


def _cert_out(request: Request, cert: Certificate) -> CertificateOut:
    url = None
    if cert.status == CertificateStatus.SUCCESS:
        url = str(request.url_for("download_certificate", certificate_id=cert.id))
    return CertificateOut(
        id=cert.id,
        position=cert.position,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        status=cert.status,
        error=cert.error,
        download_url=url,
    )


@router.post("/jobs", response_model=JobCreated, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    payload: JobCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory: sessionmaker = Depends(get_session_factory),
    certificate_dir: Path = Depends(get_certificate_dir),
):
    if len(payload.recipients) > settings.max_recipients_per_job:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"At most {settings.max_recipients_per_job} recipients per job",
        )
    job = job_service.create_job(db, payload)
    if job.status == JobStatus.PENDING:
        background_tasks.add_task(job_service.process_job, session_factory, certificate_dir, job.id)
    return JobCreated(
        id=job.id,
        status=job.status,
        total=job.total,
        status_url=str(request.url_for("get_job", job_id=job.id)),
    )


@router.get("/jobs/{job_id}", response_model=JobStatusOut, name="get_job")
def get_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    counts = job_service.count_by_status(db, job.id)
    succeeded = counts[CertificateStatus.SUCCESS]
    failed = counts[CertificateStatus.FAILED]
    done = succeeded + failed
    return JobStatusOut(
        id=job.id,
        status=job.status,
        title=job.title,
        total=job.total,
        succeeded=succeeded,
        failed=failed,
        pending=counts[CertificateStatus.PENDING],
        progress_percent=round(100 * done / job.total, 1) if job.total else 100.0,
        created_at=job.created_at,
        completed_at=job.completed_at,
        download_url=(
            str(request.url_for("download_job_zip", job_id=job.id)) if succeeded else None
        ),
    )


@router.get("/jobs/{job_id}/certificates", response_model=list[CertificateOut])
def list_certificates(
    job_id: str,
    request: Request,
    status_filter: CertificateStatus | None = None,
    db: Session = Depends(get_db),
):
    job = _get_job(db, job_id)
    certs = [c for c in job.certificates if status_filter is None or c.status == status_filter]
    return [_cert_out(request, c) for c in certs]


@router.get("/certificates/{certificate_id}/download", name="download_certificate")
def download_certificate(certificate_id: str, db: Session = Depends(get_db)):
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Certificate not found")
    if cert.status == CertificateStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Certificate is not generated yet")
    if cert.status == CertificateStatus.FAILED or not cert.file_path or not Path(cert.file_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No file available: {cert.error or 'file missing'}")
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in (cert.recipient_name or "certificate"))
    return FileResponse(cert.file_path, media_type="application/pdf", filename=f"{safe_name}_{cert.position}.pdf")


@router.get("/jobs/{job_id}/download", name="download_job_zip")
def download_job_zip(job_id: str, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    if job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is still processing")
    ready = [c for c in job.certificates if c.status == CertificateStatus.SUCCESS and c.file_path]
    if not ready:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No certificates were generated for this job")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:  # PDFs are already compressed
        for c in ready:
            if Path(c.file_path).exists():
                zf.write(c.file_path, arcname=f"{c.position:04d}_{Path(c.file_path).name}")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job.id}.zip"'},
    )
