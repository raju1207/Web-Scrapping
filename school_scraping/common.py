"""Safe raw storage, source identifiers and bounded access handling."""
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


class ManualIntervention(RuntimeError):
    """Authorized access or manual CAPTCHA completion is required."""


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", str(value).casefold().replace("&", "and"))


def safe_filename(value):
    value = re.sub(r'[<>:"/\\|?*]', "", str(value).strip())
    return re.sub(r"\s+", "_", value).strip(". ")[:150] or "unknown"


def parse_values(values):
    return list(dict.fromkeys(part.strip() for value in (values or [])
                              for part in value.split(",") if part.strip()))


def pin(value):
    text = str(value or "").strip()
    return text if re.fullmatch(r"[1-9][0-9]{5}", text) else None


def source_pin(value):
    """Normalize a dedicated source field; configured PINs remain strict."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"[0-9][0-9\s-]*[0-9]", text) or text.count("-") > 1:
        return None
    return pin(re.sub(r"[\s-]", "", text))


def check_access(text, status=200):
    lower = str(text).lower()
    phrases = ("invalid captcha", "captcha required", "verify you are human",
               "access denied", "request blocked", "unusual traffic",
               "cf-chl-", "challenge-platform", "just a moment...")
    challenge_input = re.search(r"<(?:input|iframe)[^>]*(?:captcha|recaptcha|hcaptcha)", lower)
    if status in (401, 403) or any(p in lower for p in phrases) or challenge_input:
        raise ManualIntervention(
            "CAPTCHA or access restriction detected. Complete the official website's "
            "manual access process, then rerun the same command to resume. No automatic retry.")


def read_json(path):
    with Path(path).open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".scrape-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=4, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_report(report_dir, source, report):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    path = Path(report_dir) / source / f"run_{stamp}_{uuid.uuid4().hex[:8]}.json"
    atomic_json(path, report)
    return path


class SourceLock:
    """OS-held process lock; a crashed process cannot leave a stale locked job."""
    def __init__(self, data_dir, source):
        self.path = Path(data_dir) / ".scraper_locks" / (safe_filename(source) + ".lock")
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, 2)
        if not self.handle.tell():
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            self.handle = None
            raise RuntimeError(f"Job already running: {self.path.stem}") from error
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def record_identifier(source, record):
    if not isinstance(record, dict):
        return ""
    meta = record.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    if source == "udise":
        return str(meta.get("udise_code") or "").strip()
    details = record.get("school_details")
    details = details if isinstance(details, dict) else {}
    return str(meta.get("affiliation_number") or details.get("Affiliation Number") or "").strip()


def complete_record(source, record):
    if not isinstance(record, dict) or not record_identifier(source, record):
        return False
    if source == "udise":
        details_complete = all(isinstance(record.get(key), dict) and record[key]
                   for key in ("school_profile", "student_teacher_statistics",
                               "infrastructure_facilities", "report_card"))
        meta = record.get("metadata")
        needs_history = isinstance(meta, dict) and bool(meta.get("school_id"))
        return details_complete and (not needs_history or record.get("school_history") is not None)
    details = record.get("school_details")
    return bool(isinstance(details, dict) and details.get("Affiliation Number")
                and (details.get("Name of Institution") or details.get("School Name")))


class RawStore:
    """Index source IDs globally; retain old bytes before an intentional refresh."""
    def __init__(self, source, data_dir, academic_year=None, refresh=False):
        self.source = source
        self.data_dir = Path(data_dir)
        self.academic_year = academic_year
        self.refresh = refresh
        self.index = {}
        self.invalid_files = []
        patterns = (["CBSE_SARAS/*/schools/*.json"] if source == "saras"
                    else [f"*/{academic_year or '*'}/*.json"])
        for pattern in patterns:
            for path in sorted(self.data_dir.glob(pattern)):
                if path.name.startswith("_"):
                    continue
                try:
                    record = read_json(path)
                    if not isinstance(record, dict):
                        continue
                    identifier = record_identifier(source, record)
                    if not identifier:
                        continue
                    old = self.index.get(identifier)
                    if old is None or (complete_record(source, record)
                                       and not complete_record(source, read_json(old))):
                        self.index[identifier] = path
                except (OSError, ValueError, TypeError, AttributeError):
                    self.invalid_files.append(str(path))

    def find(self, identifier):
        return self.index.get(str(identifier))

    def existing(self, identifier):
        path = self.find(identifier)
        if path:
            record = read_json(path)
            if complete_record(self.source, record):
                return record
        return None

    def save(self, identifier, record, path):
        identifier = str(identifier)
        if record_identifier(self.source, record) != identifier:
            raise ValueError("Source identifier mismatch; raw file not written")
        if not complete_record(self.source, record):
            raise ValueError("Incomplete source record; raw file not written")
        old_path = self.find(identifier)
        if old_path:
            old = read_json(old_path)
            if complete_record(self.source, old) and not self.refresh:
                return "skipped"
            keys = (("school_summary", "school_profile", "student_teacher_statistics",
                     "infrastructure_facilities", "report_card", "school_history")
                    if self.source == "udise" else ("school_details", "links"))
            if all(old.get(key) == record.get(key) for key in keys):
                return "unchanged"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            backup = self.data_dir / "refresh_backups" / self.source.upper() / f"{old_path.stem}_{stamp}_{uuid.uuid4().hex[:8]}.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_path, backup)
            if backup.read_bytes() != old_path.read_bytes():
                raise OSError("Backup verification failed; original preserved")
            atomic_json(old_path, record)
            return "updated"
        path = Path(path)
        if path.exists():
            raise FileExistsError(f"Unindexed raw file already exists; preserved: {path}")
        atomic_json(path, record)
        self.index[identifier] = path
        return "success"
