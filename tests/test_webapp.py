from __future__ import annotations

import time
from pathlib import Path

from reportlab.pdfgen import canvas

from schemadiff.webapp import create_app


def make_schematic_pdf(path: Path, changed: bool) -> None:
    document = canvas.Canvas(str(path), pagesize=(612, 400))
    for page_number in (1, 2):
        document.setFont("Helvetica-Bold", 18)
        document.drawString(40, 360, f"Demo schematic sheet {page_number}")
        document.setFont("Helvetica", 11)
        document.drawString(40, 330, "Offline revision comparison fixture")
        document.rect(45, 245, 500, 55)
        document.drawString(65, 275, "R1")
        document.drawString(105, 275, "100K" if page_number == 1 or not changed else "10K")
        document.drawString(160, 275, "C2")
        document.drawString(200, 275, "100nF")
        document.drawString(260, 275, "U3")
        document.drawString(300, 275, "VCC")
        document.line(65, 220, 500, 220)
        document.line(250, 220, 250, 175)
        document.drawString(65, 185, "Connector J1")
        document.drawString(310, 185, "NET_A")
        document.showPage()
    document.save()


def test_web_index_and_health(tmp_path: Path) -> None:
    app = create_app(tmp_path / "web", max_workers=1)
    app.config.update(TESTING=True)
    client = app.test_client()
    index = client.get("/")
    assert index.status_code == 200
    index_html = index.get_data(as_text=True)
    assert "电子原理图PDF差异扫描" in index_html
    assert "发现版本差异，直接定位到原理图证据" not in index_html
    assert "job.status === 'rejected'" in index_html
    assert 'id="historyPanel"' in index_html
    assert 'id="backToUpload"' in index_html
    assert "window.location.assign(job.links.report)" not in index_html
    health = client.get("/api/health")
    assert health.status_code == 200
    payload = health.get_json()
    assert payload["status"] == "ok"
    assert payload["offline"] is True


def test_web_rejects_missing_files(tmp_path: Path) -> None:
    app = create_app(tmp_path / "web", max_workers=1)
    app.config.update(TESTING=True)
    response = app.test_client().post("/api/jobs", data={})
    assert response.status_code == 400
    assert "同时上传" in response.get_json()["error"]


def test_web_reverse_proxy_prefix(tmp_path: Path) -> None:
    app = create_app(tmp_path / "web", prefix="/schemadiff-test")
    app.config.update(TESTING=True)
    client = app.test_client()
    root = client.get("/")
    assert root.status_code in {301, 302, 308}
    assert root.headers["Location"].endswith("/schemadiff-test/")
    page = client.get("/schemadiff-test/")
    assert page.status_code == 200
    assert 'const BASE_PATH = "/schemadiff-test"' in page.get_data(as_text=True)
    health = client.get("/schemadiff-test/api/health")
    assert health.status_code == 200


def test_web_compares_uploaded_pdfs(tmp_path: Path) -> None:
    old_pdf = tmp_path / "old.pdf"
    new_pdf = tmp_path / "new.pdf"
    make_schematic_pdf(old_pdf, changed=False)
    make_schematic_pdf(new_pdf, changed=True)
    app = create_app(tmp_path / "web", max_workers=1)
    app.config.update(TESTING=True)
    client = app.test_client()
    with old_pdf.open("rb") as old_stream, new_pdf.open("rb") as new_stream:
        response = client.post(
            "/api/jobs",
            data={
                "old": (old_stream, old_pdf.name),
                "new": (new_stream, new_pdf.name),
                "dpi": "150",
                "alignment": "auto",
                "ocr": "never",
                "ocr_language": "eng",
            },
            content_type="multipart/form-data",
        )
    assert response.status_code == 202
    job_id = response.get_json()["job_id"]
    status = None
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        status_response = client.get(f"/api/jobs/{job_id}")
        assert status_response.status_code == 200
        status = status_response.get_json()
        if status["status"] in {"complete", "error", "rejected"}:
            break
        time.sleep(0.15)
    assert status is not None
    assert status["status"] == "complete", status.get("error")
    assert status["summary"]["changed_pages"] >= 1
    assert status["summary"]["change_count"] >= 1
    assert status["summary"]["document_compatibility"]["status"] in {"pass", "review"}
    history = client.get("/api/jobs?limit=10")
    assert history.status_code == 200
    history_jobs = history.get_json()["jobs"]
    assert history_jobs
    assert history_jobs[0]["job_id"] == job_id
    assert history_jobs[0]["status"] == "complete"
    restarted = create_app(tmp_path / "web", max_workers=1)
    restarted.config.update(TESTING=True)
    persisted = restarted.test_client().get("/api/jobs?limit=10").get_json()["jobs"]
    assert any(item["job_id"] == job_id for item in persisted)

    report = client.get(status["links"]["report"])
    assert report.status_code == 200
    report_html = report.get_data(as_text=True)
    assert "原理图修订比对报告" in report_html
    assert 'class="diff-table"' in report_html
    assert "data:image/webp;base64," in report_html
    assert 'src="assets/' not in report_html
    assert 'id="backLink"' not in report_html
    assert 'id="returnButton"' in report_html
    assert "返回比对页" in report_html
    assert 'id="zoomOut"' not in report_html
    assert "addEventListener('wheel'" in report_html
    assert 'before_page_detail_image' in report_html
    assert "loadDetailImage" in report_html
    assert "frame-ancestors" in report.headers["Content-Security-Policy"]

    assert "download" not in status["links"]
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 404
