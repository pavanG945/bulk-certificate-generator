from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models import CertificateStatus, JobStatus


class RecipientIn(BaseModel):
    """Validated per recipient so one bad entry does not reject the whole request."""

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class JobCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="Course or event name")
    issuer: str = Field(default="Certificate Authority", min_length=1, max_length=200)
    issue_date: date = Field(default_factory=date.today)
    # Kept loose on purpose: each item is validated individually by the service.
    recipients: list[Any] = Field(min_length=1)


class JobCreated(BaseModel):
    id: str
    status: JobStatus
    total: int
    status_url: str


class JobStatusOut(BaseModel):
    id: str
    status: JobStatus
    title: str
    total: int
    succeeded: int
    failed: int
    pending: int
    progress_percent: float
    created_at: datetime
    completed_at: datetime | None
    download_url: str | None = Field(description="ZIP of all successful certificates, once available")


class CertificateOut(BaseModel):
    id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    status: CertificateStatus
    error: str | None
    download_url: str | None
