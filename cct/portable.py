"""User-controlled profile copies, private diagnostics, and finished-run import.

Nothing in this module opens device ports, applies a profile, or resumes a run.
"""

import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .contracts import CHANNELS, DEVICE_IDS
from .engine import STAGES, validate_config


MAX_JSON_BYTES = 1024 * 1024
MAX_IMPORT_BYTES = 1024 * 1024 * 1024
MAX_IMPORT_ROWS = 2_000_000
MAX_IMPORT_SECONDS = 60.0
FINISHED_STATUSES = frozenset({"complete", "completed", "aborted", "fault", "interrupted"})


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _token():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex


def _json_bytes(value):
    try:
        encoded = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError, OverflowError) as error:
        raise ValueError("Settings must contain ordinary JSON values and finite numbers.") from error
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("Settings exceed the 1 MiB JSON limit.")
    return encoded


def _publish_new(temporary, destination):
    """Publish a complete file atomically, refusing an existing destination."""
    if os.name == "nt":
        temporary.rename(destination)  # Windows rename refuses existing targets.
    else:
        os.link(temporary, destination)  # Atomic and exclusive on POSIX.
        temporary.unlink()


def _write_new(destination, content):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".writing_", dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _publish_new(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def save_apparatus_profile(data_root, profile):
    """Save a new immutable-by-convention copy; never edit the selected source.

    Commissioning flags are preserved exactly as supplied. A caller that changes
    apparatus bindings must clear commissioning before passing the edited copy.
    """
    if not isinstance(profile, dict):
        raise ValueError("An apparatus profile must be a JSON object.")
    content = _json_bytes(profile)
    destination = Path(data_root).resolve() / "profiles" / f"apparatus_{_token()}.json"
    _write_new(destination, content)
    return destination


def _number(value):
    try:
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    except OverflowError:
        return None


def _issue_code(value):
    # Emit a fixed vocabulary rather than attempting to redact arbitrary text.
    text = str(value).casefold()
    for needle, code in (("profile", "profile_configuration"), ("handshake", "bridge_handshake"),
                         ("own", "control_ownership"), ("s3", "output_board_connection"),
                         ("stale", "stale_readings"), ("disconnect", "device_connection"),
                         ("temperature", "temperature_limit"), ("timeout", "operation_timeout"),
                         ("ack", "command_acknowledgement"), ("disk", "recording_storage"),
                         ("record", "recording_storage"), ("connect", "device_connection")):
        if needle in text:
            return code
    return "readiness_issue"


def _choice(value, choices):
    return value if isinstance(value, str) and value in choices else "unknown"


def export_support_bundle(data_root, snapshot):
    """Export privacy-first diagnostics. Raw logs are deliberately omitted."""
    if not isinstance(snapshot, dict):
        raise ValueError("Support diagnostics require an application snapshot object.")
    safe_state = {}
    safe_state["mode"] = _choice(snapshot.get("mode"), {"simulation", "hardware"})
    safe_state["stage"] = _choice(snapshot.get("stage"), STAGES)
    safe_state["outcome"] = _choice(snapshot.get("outcome"), FINISHED_STATUSES | {"", "stopping"})
    for key in ("connected", "running"):
        safe_state[key] = bool(snapshot.get(key, False))
    for key in ("cycle", "total_cycles", "elapsed_s", "stage_elapsed_s", "stage_progress"):
        safe_state[key] = _number(snapshot.get(key))
    devices = snapshot.get("devices", {})
    devices = devices if isinstance(devices, dict) else {}
    safe_state["devices"] = {
        device: {"connected": bool(devices.get(device, {}).get("connected", False)),
                 "fresh": bool(devices.get(device, {}).get("fresh", False))}
        for device in DEVICE_IDS if isinstance(devices.get(device, {}), dict)
    }
    blockers = snapshot.get("blockers", [])
    warnings = snapshot.get("warnings", [])
    blockers = blockers if isinstance(blockers, (list, tuple)) else []
    warnings = warnings if isinstance(warnings, (list, tuple)) else []
    safe_state["readiness"] = {"blocker_count": len(blockers), "warning_count": len(warnings),
                                "blocker_codes": sorted({_issue_code(item) for item in blockers[:100]})}
    outputs = snapshot.get("outputs", {})
    outputs = outputs if isinstance(outputs, dict) else {}
    safe_state["commands"] = {"pending_count": _number(outputs.get("pending_count", 0)),
                               "fault_present": bool(outputs.get("command_fault")),
                               "session_owned": bool(outputs.get("owned", False)),
                               "output_board_online": bool(outputs.get("s3_online", False))}
    dependencies = {}
    for package in ("PySide6", "pyqtgraph", "numpy", "pyserial"):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = "not installed"
    report = {"format": "cct-support-v1", "created_utc": _utc(), "app_version": __version__,
              "platform": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
              "dependencies": dependencies, "state": safe_state,
              "privacy": {"raw_logs_included": False, "measurements_included": False, "profiles_included": False,
                          "free_text_included": False,
                          "omitted": ["raw application logs", "instrument replies", "measurements", "event messages", "profile and gas routes",
                                      "serial identifiers", "operator and sample information", "local paths", "run identifiers"]}}
    root = Path(data_root).resolve()
    destination = root / "support" / f"CCT-support_{_token()}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".support_", suffix=".zip", dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("diagnostics.json", _json_bytes(report))
            archive.writestr("README.txt", "CCT Control support diagnostics\n\nThis archive contains application/dependency versions and a sanitized operational/readiness report.\nRaw logs, measurements, event text, profiles, serial identifiers, experiment identities and local paths are deliberately omitted.\nReview diagnostics.json before sharing. This archive is not an experiment backup.\n")
        _publish_new(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination


def _is_link(path):
    metadata = path.lstat()
    return path.is_symlink() or bool(getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _strict_json(text, label):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError(f"{label} is missing or exceeds the 1 MiB record limit.")
    def reject_constant(value):
        raise ValueError(f"Non-finite JSON number: {value}")
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Non-finite JSON number")
        return result
    try:
        value = json.loads(text, parse_constant=reject_constant, parse_float=finite_float)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError(f"{label} is not valid finite JSON.") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return value


def _schema(connection):
    expected = {
        "metadata": "CREATETABLEMETADATA(KEYTEXTPRIMARYKEY,VALUETEXTNOTNULL)",
        "samples": "CREATETABLESAMPLES(IDINTEGERPRIMARYKEY,DATATEXTNOTNULL)",
        "events": "CREATETABLEEVENTS(IDINTEGERPRIMARYKEY,DATATEXTNOTNULL)",
    }
    rows = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master").fetchall()
    tables = {}
    for kind, name, owner, sql in rows:
        if kind == "index" and name == "sqlite_autoindex_metadata_1" and owner == "metadata" and sql is None:
            continue
        if kind != "table" or name not in expected:
            raise ValueError("The selected database contains unsupported tables, views, indexes or triggers.")
        normalized = re.sub(r"\s+", "", sql or "").upper().rstrip(";")
        if normalized != expected[name]:
            raise ValueError(f"Unsupported {name} table schema.")
        tables[name] = normalized
    if set(tables) != set(expected):
        raise ValueError("The selected database is not a supported CCT experiment record.")


def _validate_rowspec(value, table):
    """Protect history/CSV consumers from malformed imported field types."""
    if table == "samples":
        if _number(value.get("elapsed_s")) is None or value["elapsed_s"] < 0:
            raise ValueError("Imported measurements need a nonnegative elapsed time.")
        if not isinstance(value.get("stage"), str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value["stage"]):
            raise ValueError("Imported measurements contain an invalid stage.")
        cycle = value.get("cycle")
        if not isinstance(cycle, int) or isinstance(cycle, bool) or not 0 <= cycle <= 1000:
            raise ValueError("Imported measurements contain an invalid cycle number.")
        for key in (*CHANNELS, "baseline_ppm", "breakthrough_ratio", "threshold_ratio", "stage_elapsed_s"):
            if key in value and value[key] is not None and _number(value[key]) is None:
                raise ValueError(f"Imported measurements contain a nonnumeric {key} field.")
        for key in ("ages", "outputs", "raw", "quality"):
            if key in value and not isinstance(value[key], dict):
                raise ValueError(f"Imported measurements contain invalid {key} information.")
    else:
        for key in ("event", "message", "level", "stage", "timestamp_utc"):
            if key in value and not isinstance(value[key], str):
                raise ValueError(f"Imported events contain an invalid {key} field.")
        for key in ("elapsed_s", "stage_elapsed_s"):
            if key in value and value[key] is not None and (_number(value[key]) is None or value[key] < 0):
                raise ValueError(f"Imported events contain an invalid {key} field.")
        if "cycle" in value and (type(value["cycle"]) is not int or not 0 <= value["cycle"] <= 1000):
            raise ValueError("Imported events contain an invalid cycle number.")


def _secure_connection(connection):
    connection.execute("PRAGMA trusted_schema=OFF")
    if hasattr(connection, "enable_load_extension"):
        connection.enable_load_extension(False)
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_JSON_BYTES)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_JSON_BYTES)
    connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)


def _validate_database(connection, source_id, deadline):
    _schema(connection)
    if connection.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
        raise ValueError("The selected database did not pass SQLite integrity checks.")
    metadata_rows = connection.execute("SELECT key,value FROM metadata LIMIT 17").fetchall()
    if len(metadata_rows) > 16 or any(key not in {"run", "recipe", "summary"} for key, _ in metadata_rows):
        raise ValueError("The selected record contains unsupported metadata.")
    records = {key: _strict_json(value, f"{key} metadata") for key, value in metadata_rows}
    if "run" not in records or "recipe" not in records:
        raise ValueError("Run metadata or frozen test settings are missing.")
    try:
        settings_errors = validate_config(records["recipe"])
    except (TypeError, ValueError, OverflowError):
        settings_errors = ["Invalid frozen settings."]
    threshold = records["recipe"].get("breakthrough_pct")
    if threshold is not None and _number(threshold) is None:
        settings_errors.append("Breakthrough threshold must be numeric or disabled.")
    if settings_errors:
        raise ValueError("The source experiment has invalid frozen test settings: " + settings_errors[0])
    metadata = records["run"]
    if type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1:
        raise ValueError("This experiment schema version is not supported.")
    if not isinstance(metadata.get("status"), str) or metadata["status"] not in FINISHED_STATUSES:
        raise ValueError("Only finished, stopped, faulted or interrupted experiments can be imported. Close and finalize the source experiment first.")
    run_id = metadata.get("id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", run_id) or run_id in {".", ".."}:
        raise ValueError("Source experiment identity is invalid.")
    if run_id != source_id:
        raise ValueError("Select the original experiment folder whose name matches its saved run ID.")
    for key in ("notebook_id", "sample_id", "started_at"):
        if not isinstance(metadata.get(key), str) or not metadata[key] or len(metadata[key]) > 1000:
            raise ValueError(f"The source experiment has invalid {key} metadata.")
    for key in ("operator", "notes", "app_version", "mode", "test_type"):
        if key in metadata and not isinstance(metadata[key], str):
            raise ValueError(f"The source experiment has invalid {key} metadata.")
    cycles = metadata.get("cycles_completed", 0)
    if type(cycles) is not int or not 0 <= cycles <= 1000:
        raise ValueError("The source experiment has an invalid completed-cycle count.")
    summary_cycles = records.get("summary", {}).get("cycles", [])
    if not isinstance(summary_cycles, list) or not all(isinstance(cycle, dict) for cycle in summary_cycles):
        raise ValueError("The source experiment has invalid cycle-summary metadata.")
    duration = records.get("summary", {}).get("measurement_duration_s")
    if duration is not None and (_number(duration) is None or duration < 0):
        raise ValueError("The source experiment has invalid measurement-duration metadata.")
    for cycle in summary_cycles:
        if "cycle" in cycle and (type(cycle["cycle"]) is not int or not 0 <= cycle["cycle"] <= 1000):
            raise ValueError("The source experiment has an invalid summary cycle number.")
        if "status" in cycle and (not isinstance(cycle["status"], str) or len(cycle["status"]) > 100):
            raise ValueError("The source experiment has an invalid summary cycle status.")
        for key in ("inlet_co2_ppm", "inlet_std_ppm", "threshold_ratio", "breakthrough_elapsed_s", "breakthrough_time_s"):
            if key in cycle and cycle[key] is not None and _number(cycle[key]) is None:
                raise ValueError(f"The source experiment has invalid summary {key} metadata.")
    for table in ("samples", "events"):
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count > MAX_IMPORT_ROWS:
            raise ValueError("The selected experiment exceeds the supported row limit.")
        for index, (data,) in enumerate(connection.execute(f"SELECT data FROM {table} ORDER BY id")):
            _validate_rowspec(_strict_json(data, f"{table} row"), table)
            if index % 1000 == 0 and time.monotonic() > deadline:
                raise ValueError("Import validation exceeded its time limit.")
    records.setdefault("summary", {})
    return records


def import_completed_run(data_root, source_folder):
    """Copy one finished experiment through SQLite backup, with no control effects."""
    source = Path(source_folder).absolute()
    if not source.is_dir() or _is_link(source):
        raise ValueError("Select an ordinary completed experiment folder, not a symbolic link.")
    database = source / "run.sqlite"
    if not database.is_file() or _is_link(database):
        raise ValueError("The selected folder must contain an ordinary run.sqlite file.")
    if database.stat().st_size > MAX_IMPORT_BYTES:
        raise ValueError("The selected experiment database exceeds the 1 GiB import limit.")
    for suffix in ("-wal", "-journal", "-shm"):
        sidecar = source / ("run.sqlite" + suffix)
        if sidecar.exists() and (_is_link(sidecar) or (suffix != "-shm" and sidecar.stat().st_size > 0)):
            raise ValueError("The source database still has active journal files. Close and finalize it before importing.")
    root = Path(data_root).resolve()
    runs_root = root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    runs_root = runs_root.resolve()
    temporary = None
    deadline = time.monotonic() + MAX_IMPORT_SECONDS
    def progress():
        return int(time.monotonic() > deadline)
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=2)) as reader:
            reader.execute("PRAGMA query_only=ON")
            _secure_connection(reader)
            reader.set_progress_handler(progress, 10000)
            records = _validate_database(reader, source.name, deadline)
            source_id = records["run"]["id"]
            run_id = source_id[:100] + "_import_" + uuid.uuid4().hex
            destination = runs_root / run_id
            if destination.exists():
                raise FileExistsError("The generated import destination already exists; retry import.")
            temporary = Path(tempfile.mkdtemp(prefix=".importing_", dir=runs_root))
            with closing(sqlite3.connect(temporary / "run.sqlite", timeout=2)) as writer:
                def backup_progress(status, remaining, total):
                    if time.monotonic() > deadline:
                        raise ValueError("Database backup exceeded its time limit.")
                reader.backup(writer, pages=256, progress=backup_progress, sleep=0)
                _secure_connection(writer)
                writer.set_progress_handler(progress, 10000)
                records = _validate_database(writer, source.name, deadline)
                metadata = records["run"]
                previous_import = metadata.get("source_import", {})
                metadata["id"], metadata["path"] = run_id, str(destination)
                metadata["source_import"] = {"original_id": previous_import.get("original_id", source_id) if isinstance(previous_import, dict) else source_id,
                                             "copied_run_id": source_id, "imported_at": _utc(), "original_status": metadata["status"]}
                writer.execute("UPDATE metadata SET value=? WHERE key='run'", (_json_bytes(metadata).decode("utf-8"),))
                # Retain measurement values while keeping exported run identity consistent.
                for table in ("samples", "events"):
                    cursor = writer.execute(f"SELECT id,data FROM {table} ORDER BY id")
                    for row_id, text in cursor:
                        row = _strict_json(text, f"{table} row")
                        if "run_id" in row:
                            row.setdefault("source_run_id", row["run_id"])
                            row["run_id"] = run_id
                            writer.execute(f"UPDATE {table} SET data=? WHERE id=?", (_json_bytes(row).decode("utf-8"), row_id))
                writer.commit()
                writer.execute("PRAGMA journal_mode=DELETE")
            for name, value in (("metadata.json", metadata), ("recipe.json", records["recipe"]),
                                ("test_settings.json", records["recipe"]), ("summary.json", records["summary"])):
                (temporary / name).write_bytes(_json_bytes(value))
            if temporary.resolve().parent != runs_root or destination.parent.resolve() != runs_root:
                raise ValueError("Import destination escaped the experiment collection.")
            temporary.rename(destination)
            temporary = None
            return run_id
    except sqlite3.Error as error:
        raise ValueError("The selected file is not a valid, supported completed CCT database.") from error
    finally:
        if temporary is not None and temporary.exists():
            if temporary.resolve().parent == runs_root and temporary.name.startswith(".importing_") and not _is_link(temporary):
                shutil.rmtree(temporary)
