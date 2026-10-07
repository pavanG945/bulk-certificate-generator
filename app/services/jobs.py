import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import JobCreate, RecipientIn
from app.services import certificate_generator

logger = logging.getLogger(__name__)
_recipient_adapter = TypeAdapter(RecipientIn)


def _format_validation_error(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())


def create_job(db: Session, payload: JobCreate) -> Job:
    """Persist the job and one row per recipient.

    Recipients that fail validation are stored as FAILED with the reason,
    so the client can see them in the job result instead of losing them.
    """
    job = Job(
        title=payload.title,
        issuer=payload.issuer,
        issue_date=payload.issue_date,
        total=len(payload.recipients),
    )
    for position, raw in enumerate(payload.recipients):
        cert = Certificate(position=position)
        if not isinstance(raw, dict):
            cert.status = CertificateStatus.FAILED
            cert.error = "Invalid recipient: must be an object with 'name' and optional 'email'"
        else:
            try:
                recipient = _recipient_adapter.validate_python(raw)
                cert.recipient_name = recipient.name
                cert.recipient_email = recipient.email
            except ValidationError as exc:
                cert.status = CertificateStatus.FAILED
                cert.error = f"Invalid recipient: {_format_validation_error(exc)}"
                name = raw.get("name")
                cert.recipient_name = str(name)[:200] if name is not None else None
        job.certificates.append(cert)

    if all(c.status == CertificateStatus.FAILED for c in job.certificates):
        _finish(job)
    db.add(job)
    db.commit()
    return job


def _finish(job: Job) -> None:
    succeeded = sum(c.status == CertificateStatus.SUCCESS for c in job.certificates)
    if succeeded == 0:
        job.status = JobStatus.FAILED
    elif succeeded == job.total:
        job.status = JobStatus.COMPLETED
    else:
        job.status = JobStatus.COMPLETED_WITH_ERRORS
    job.completed_at = datetime.now(timezone.utc)


def process_job(session_factory: sessionmaker, certificate_dir: Path, job_id: str) -> None:
    """Generate every pending certificate of a job (runs as a background task).

    Each certificate is isolated: an exception marks only that row FAILED.
    Progress is committed per certificate so status polling sees it live.
    """
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None or job.status != JobStatus.PENDING:
            return
        job.status = JobStatus.PROCESSING
        db.commit()

        try:
            out_dir = Path(certificate_dir) / job.id
            out_dir.mkdir(parents=True, exist_ok=True)
            pending = db.scalars(
                select(Certificate)
                .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.PENDING)
                .order_by(Certificate.position)
            ).all()
            for cert in pending:
                try:
                    pdf = certificate_generator.generate_certificate_pdf(
                        recipient_name=cert.recipient_name,
                        title=job.title,
                        issuer=job.issuer,
                        issue_date=job.issue_date,
                    )
                    path = out_dir / f"{cert.id}.pdf"
                    path.write_bytes(pdf)
                    cert.file_path = str(path)
                    cert.status = CertificateStatus.SUCCESS
                except Exception as exc:  # one bad certificate must not stop the job
                    logger.warning("Certificate %s failed: %s", cert.id, exc)
                    cert.status = CertificateStatus.FAILED
                    cert.error = f"Generation failed: {exc}"[:500]
                db.commit()
        except Exception:
            # Unexpected infrastructure error (e.g. disk): never leave the job stuck.
            logger.exception("Job %s aborted", job_id)
            db.rollback()
            for cert in db.scalars(
                select(Certificate).where(
                    Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING
                )
            ):
                cert.status = CertificateStatus.FAILED
                cert.error = "Job aborted before this certificate was generated"
            db.commit()

        db.refresh(job)
        _finish(job)
        db.commit()


def count_by_status(db: Session, job_id: str) -> dict[CertificateStatus, int]:
    rows = db.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {status: 0 for status in CertificateStatus}
    counts.update({status: n for status, n in rows})
    return counts
