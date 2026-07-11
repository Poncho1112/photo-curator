from engine.database.models import PhotoRecord
from app.workers.scan_worker import ScanJob


def test_scan_job_cancellation_stops_between_files(tmp_path, monkeypatch):
    paths = [tmp_path / f"{index}.jpg" for index in range(3)]
    for path in paths:
        path.write_bytes(b"x")
    monkeypatch.setattr("app.workers.scan_worker.scan_photos", lambda root: paths)
    monkeypatch.setattr(ScanJob, "_read_record", staticmethod(lambda path: PhotoRecord(str(path), "a" * 64, 1)))
    job = ScanJob([tmp_path])

    def progress(current, total, path):
        if current == 1:
            job.cancel()

    records = job.run(progress=progress)
    assert len(records) == 1


def test_scan_job_reports_bad_file_and_continues(tmp_path, monkeypatch):
    bad, good = tmp_path / "bad.jpg", tmp_path / "good.jpg"
    monkeypatch.setattr("app.workers.scan_worker.scan_photos", lambda root: [bad, good])

    def read(path):
        if path == bad.resolve():
            raise ValueError("corrupt")
        return PhotoRecord(str(path), "a" * 64, 1)

    monkeypatch.setattr(ScanJob, "_read_record", staticmethod(read))
    errors = []
    records = ScanJob([tmp_path]).run(error=lambda path, message: errors.append((path, message)))
    assert len(records) == 1
    assert errors[0][1] == "corrupt"

