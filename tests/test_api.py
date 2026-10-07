import io
import zipfile

from app.services import certificate_generator
from tests.conftest import make_payload

GOOD = [
    {"name": "Asha Rao", "email": "asha@example.com"},
    {"name": "José Núñez"},
    {"name": "Li Wei", "email": "li@example.com"},
]


# --- Creating a generation job -------------------------------------------------

def test_create_job_returns_accepted_with_status_url(client):
    r = client.post("/api/jobs", json=make_payload(GOOD))
    assert r.status_code == 202
    body = r.json()
    assert body["total"] == 3
    assert body["status_url"].endswith(f"/api/jobs/{body['id']}")


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/nope").status_code == 404


# --- Input validation ----------------------------------------------------------

def test_request_level_validation_rejects_bad_requests(client):
    assert client.post("/api/jobs", json=make_payload([])).status_code == 422  # empty list
    assert client.post("/api/jobs", json=make_payload(GOOD, title="")).status_code == 422
    assert client.post("/api/jobs", json={"recipients": GOOD}).status_code == 422  # no title
    assert client.post("/api/jobs", json=make_payload(GOOD, issue_date="not-a-date")).status_code == 422


def test_too_many_recipients_rejected(client, monkeypatch):
    monkeypatch.setattr("app.routers.jobs.settings", type("S", (), {"max_recipients_per_job": 2})())
    assert client.post("/api/jobs", json=make_payload(GOOD)).status_code == 413


def test_invalid_recipients_are_reported_per_item_not_rejected(client):
    recipients = [
        {"name": "Valid Person", "email": "ok@example.com"},
        {"name": "   "},  # blank name
        {"email": "noname@example.com"},  # missing name
        {"name": "Bad Email", "email": "not-an-email"},
        "just a string",  # wrong shape
    ]
    job_id = client.post("/api/jobs", json=make_payload(recipients)).json()["id"]

    job = client.get(f"/api/jobs/{job_id}").json()
    assert (job["succeeded"], job["failed"], job["status"]) == (1, 4, "COMPLETED_WITH_ERRORS")

    certs = client.get(f"/api/jobs/{job_id}/certificates").json()
    assert [c["status"] for c in certs] == ["SUCCESS", "FAILED", "FAILED", "FAILED", "FAILED"]
    assert all(c["error"].startswith("Invalid recipient") for c in certs[1:])
    assert all(c["download_url"] is None for c in certs[1:])


def test_job_with_only_invalid_recipients_fails_immediately(client):
    r = client.post("/api/jobs", json=make_payload([{"name": ""}, {}]))
    assert r.status_code == 202
    assert r.json()["status"] == "FAILED"
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "FAILED" and job["failed"] == 2 and job["download_url"] is None


# --- Certificate generation & retrieval ----------------------------------------

def test_certificates_are_generated_and_downloadable_as_pdf(client):
    job_id = client.post("/api/jobs", json=make_payload(GOOD)).json()["id"]

    certs = client.get(f"/api/jobs/{job_id}/certificates").json()
    assert len(certs) == 3 and all(c["status"] == "SUCCESS" for c in certs)

    r = client.get(certs[0]["download_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_generator_embeds_recipient_details():
    from datetime import date

    pdf = certificate_generator.generate_certificate_pdf(
        recipient_name="Asha Rao", title="Python Bootcamp", issuer="Acme", issue_date=date(2026, 9, 30)
    )
    assert pdf.startswith(b"%PDF") and len(pdf) > 500


def test_download_all_as_zip(client):
    job_id = client.post("/api/jobs", json=make_payload(GOOD)).json()["id"]
    r = client.get(f"/api/jobs/{job_id}/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert len(zf.namelist()) == 3
        assert all(zf.read(n).startswith(b"%PDF") for n in zf.namelist())


def test_zip_unavailable_when_nothing_generated(client):
    job_id = client.post("/api/jobs", json=make_payload([{"name": ""}])).json()["id"]
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 404


def test_download_unknown_or_failed_certificate(client):
    assert client.get("/api/certificates/missing/download").status_code == 404
    job_id = client.post("/api/jobs", json=make_payload([{"name": ""}])).json()["id"]
    cert = client.get(f"/api/jobs/{job_id}/certificates").json()[0]
    assert cert["download_url"] is None


# --- Job status / progress -----------------------------------------------------

def test_job_status_counts_and_progress(client):
    job_id = client.post("/api/jobs", json=make_payload(GOOD)).json()["id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED"
    assert (job["total"], job["succeeded"], job["failed"], job["pending"]) == (3, 3, 0, 0)
    assert job["progress_percent"] == 100.0
    assert job["completed_at"] is not None
    assert job["download_url"].endswith(f"/api/jobs/{job_id}/download")


def test_pending_state_before_processing(client, monkeypatch):
    """If the background task has not run yet, the job reports PENDING with 0% progress."""
    monkeypatch.setattr("app.services.jobs.process_job", lambda *a, **k: None)
    job_id = client.post("/api/jobs", json=make_payload(GOOD)).json()["id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "PENDING"
    assert (job["pending"], job["progress_percent"]) == (3, 0.0)
    cert = client.get(f"/api/jobs/{job_id}/certificates").json()[0]
    assert cert["download_url"] is None
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 409


def test_certificates_can_be_filtered_by_status(client):
    job_id = client.post("/api/jobs", json=make_payload([{"name": "Ok"}, {"name": ""}])).json()["id"]
    failed = client.get(f"/api/jobs/{job_id}/certificates", params={"status_filter": "FAILED"}).json()
    assert [c["position"] for c in failed] == [1]


# --- Individual certificate failure --------------------------------------------

def test_one_generation_failure_does_not_block_others(client, monkeypatch):
    real = certificate_generator.generate_certificate_pdf

    def flaky(**kwargs):
        if kwargs["recipient_name"] == "Li Wei":
            raise RuntimeError("renderer exploded")
        return real(**kwargs)

    monkeypatch.setattr(certificate_generator, "generate_certificate_pdf", flaky)
    job_id = client.post("/api/jobs", json=make_payload(GOOD)).json()["id"]

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert (job["succeeded"], job["failed"]) == (2, 1)

    certs = {c["recipient_name"]: c for c in client.get(f"/api/jobs/{job_id}/certificates").json()}
    assert certs["Li Wei"]["status"] == "FAILED"
    assert "renderer exploded" in certs["Li Wei"]["error"]
    assert certs["Asha Rao"]["status"] == certs["José Núñez"]["status"] == "SUCCESS"
    assert client.get(certs["Asha Rao"]["download_url"]).status_code == 200

    # The ZIP contains only the successful ones.
    with zipfile.ZipFile(io.BytesIO(client.get(job["download_url"]).content)) as zf:
        assert len(zf.namelist()) == 2


def test_unrenderable_name_fails_only_that_certificate(client):
    """Built-in PDF font is Latin-1 only; a CJK name is a real, per-item failure."""
    job_id = client.post(
        "/api/jobs", json=make_payload([{"name": "Asha Rao"}, {"name": "山田太郎"}])
    ).json()["id"]
    certs = client.get(f"/api/jobs/{job_id}/certificates").json()
    assert [c["status"] for c in certs] == ["SUCCESS", "FAILED"]
    assert certs[1]["error"].startswith("Generation failed")
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "COMPLETED_WITH_ERRORS"
