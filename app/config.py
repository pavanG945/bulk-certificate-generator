import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/certificates.db")
    certificate_dir: Path = Path(os.getenv("CERTIFICATE_DIR", "./data/certificates"))
    max_recipients_per_job: int = int(os.getenv("MAX_RECIPIENTS_PER_JOB", "1000"))


settings = Settings()
