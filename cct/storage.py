"""Incremental experiment records with portable CSV exports.

The SQLite database is authoritative. CSVs are reproducible exports, never the
only copy of measurements. Each experiment has its own uniquely named folder.
"""

import csv
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .contracts import CHANNELS
from . import __version__


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _slug(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip(".-")[:64] or "run"


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class RunStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.runs_root = self.root / "runs"
        self.recipes_root = self.root / "recipes"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.recipes_root.mkdir(parents=True, exist_ok=True)
        self.connection = None
        self.run_id = None
        self.run_path = None

    def recover_interrupted(self):
        """Mark unfinished records; never reconnect or resume physical outputs."""
        recovered = []
        for db_path in self.runs_root.glob("*/run.sqlite"):
            try:
                with closing(sqlite3.connect(db_path, timeout=2.0)) as connection:
                    row = connection.execute("SELECT value FROM metadata WHERE key='run'").fetchone()
                    if not row:
                        continue
                    metadata = json.loads(row[0])
                    if metadata.get("status") != "running":
                        continue
                    metadata.update(status="interrupted", ended_at=utc_now(), reason="The app closed before the run was finalized. Automatic resume is disabled.")
                    connection.execute("UPDATE metadata SET value=? WHERE key='run'", (_json(metadata),))
                    event = {"timestamp_utc": utc_now(), "elapsed_s": None, "event": "interrupted", "level": "error", "message": metadata["reason"]}
                    connection.execute("INSERT INTO events(data) VALUES (?)", (_json(event),))
                    connection.commit()
                    recovered.append(metadata.get("id", db_path.parent.name))
            except (sqlite3.Error, OSError, ValueError):
                continue
        return recovered

    def create(self, config, mode, apparatus=None):
        if self.connection is not None:
            raise RuntimeError("An experiment record is already open.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        display_name = config.get("run_name") or config["notebook_id"]
        self.run_id = f"{_slug(display_name)}_{stamp}_{uuid.uuid4().hex[:8]}"
        self.run_path = self.runs_root / self.run_id
        self.run_path.mkdir(exist_ok=False)
        # The browser controller serializes all LabService access with one lock,
        # but HTTP actions and its acquisition timer run on different threads.
        self.connection = sqlite3.connect(self.run_path / "run.sqlite", timeout=5.0,
                                          check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE samples(id INTEGER PRIMARY KEY, data TEXT NOT NULL); CREATE TABLE events(id INTEGER PRIMARY KEY, data TEXT NOT NULL);")
        metadata = {"id": self.run_id, "schema_version": 1, "app_version": __version__, "notebook_id": config["notebook_id"],
                    "run_name": config.get("run_name") or display_name,
                    "test_type": config.get("test_type", "legacy_breakthrough"),
                    "calibrator_mode": (apparatus or {}).get("calibrator_mode", "automatic"),
                    "sample_id": config["sample_id"], "sample_mass_g": config["sample_mass_g"], "operator": config.get("operator", ""),
                    "notes": config.get("notes", ""), "mode": mode, "status": "running", "started_at": utc_now(), "cycles_completed": 0,
                    "path": str(self.run_path), "units": {key: unit for key, (_, unit, _) in CHANNELS.items()},
                    "apparatus": apparatus or {}, "time_basis": "simulated elapsed seconds" if mode == "simulation" else "monotonic elapsed seconds",
                    "timestamp_basis": "UTC receipt/write time; use elapsed_s for simulated timing" if mode == "simulation" else "UTC receipt/write time"}
        self.connection.executemany("INSERT INTO metadata(key,value) VALUES (?,?)", [("run", _json(metadata)), ("recipe", _json(config))])
        self.connection.commit()
        self._atomic_json(self.run_path / "metadata.json", metadata)
        self._atomic_json(self.run_path / "recipe.json", config)
        self._atomic_json(self.run_path / "test_settings.json", config)
        return metadata

    def append_sample(self, row):
        if self.connection is None:
            return
        row = {key: value for key, value in row.items() if key != "h2o_mmol_mol"}
        for field in ("raw", "ages", "quality"):
            if isinstance(row.get(field), dict):
                row[field] = {key: value for key, value in row[field].items() if key != "h2o_mmol_mol"}
        self.connection.execute("INSERT INTO samples(data) VALUES (?)", (_json(row),))
        self.connection.commit()

    def append_event(self, event):
        if self.connection is None:
            return
        self.connection.execute("INSERT INTO events(data) VALUES (?)", (_json(event),))
        self.connection.commit()

    def finalize(self, status, cycles_completed=0, reason="", summary=None):
        if self.connection is None:
            return
        row = self.connection.execute("SELECT value FROM metadata WHERE key='run'").fetchone()
        metadata = json.loads(row[0])
        metadata.update(status=status, ended_at=utc_now(), cycles_completed=cycles_completed, reason=reason)
        self.connection.execute("UPDATE metadata SET value=? WHERE key='run'", (_json(metadata),))
        self.connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('summary',?)", (_json(summary or {}),))
        self.connection.commit()
        self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.connection.close()
        self.connection = None
        self._atomic_json(self.run_path / "metadata.json", metadata)

    @staticmethod
    def _atomic_json(path, value):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(_json(value) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _resolve_run(self, run_id):
        if not isinstance(run_id, str) or Path(run_id).name != run_id or "/" in run_id or "\\" in run_id or run_id in {"", ".", ".."}:
            raise ValueError("Invalid experiment identifier.")
        path = (self.runs_root / run_id).resolve()
        if path.parent != self.runs_root.resolve() or not (path / "run.sqlite").is_file():
            raise ValueError("Experiment was not found in the data folder.")
        return path

    def load(self, run_id):
        path = self._resolve_run(run_id)
        with closing(sqlite3.connect((path / 'run.sqlite').as_uri() + "?mode=ro", uri=True)) as connection:
            metadata_rows = dict(connection.execute("SELECT key,value FROM metadata"))
            metadata = json.loads(metadata_rows["run"])
            metadata["recipe"] = json.loads(metadata_rows["recipe"])
            metadata["path"] = str(path)
            return {"metadata": metadata, "samples": [json.loads(row[0]) for row in connection.execute("SELECT data FROM samples ORDER BY id")],
                    "events": [json.loads(row[0]) for row in connection.execute("SELECT data FROM events ORDER BY id")],
                    "summary": json.loads(metadata_rows.get("summary", "{}"))}

    def list_runs(self):
        runs = []
        for db_path in self.runs_root.glob("*/run.sqlite"):
            try:
                with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=2.0)) as connection:
                    row = connection.execute("SELECT value FROM metadata WHERE key='run'").fetchone()
                    metadata = json.loads(row[0])
                    metadata["path"] = str(db_path.parent)
                    runs.append(metadata)
            except (sqlite3.Error, ValueError, OSError, TypeError):
                continue
        return sorted(runs, key=lambda run: run.get("started_at", ""), reverse=True)

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def export(self, run_id):
        run = self.load(run_id)
        destination = self._resolve_run(run_id) / "exports"
        destination.mkdir(exist_ok=True)
        samples = run["samples"]
        sample_fields = ["timestamp_utc", "mode", "run_id", "test_type", "notebook_id", "sample_id", "elapsed_s", "stage_elapsed_s", "stage", "cycle", *CHANNELS, "baseline_ppm", "breakthrough_ratio", "threshold_ratio", "quality_json", "ages_json", "outputs_json", "raw_json"]
        self._csv(destination / "measurements_1Hz.csv", samples, sample_fields)
        self._csv(destination / "events.csv", run["events"], ["timestamp_utc", "elapsed_s", "cycle", "stage", "level", "event", "message", "details_json"])
        phases = {}
        for sample in samples:
            phase = f"cycle_{sample['cycle']:02d}_{_slug(sample['stage'])}"
            phases.setdefault(phase, []).append(sample)
        for phase, rows in phases.items():
            self._csv(destination / f"{phase}.csv", rows, sample_fields)
        summary_rows = run["summary"].get("cycles", [])
        self._csv(destination / "cycle_summary.csv", summary_rows, ["cycle", "inlet_co2_ppm", "inlet_std_ppm", "breakthrough_elapsed_s", "breakthrough_time_s", "threshold_ratio", "status"])
        self._atomic_json(destination / "metadata.json", run["metadata"])
        self._atomic_json(destination / "recipe.json", run["metadata"]["recipe"])
        self._atomic_json(destination / "test_settings.json", run["metadata"]["recipe"])
        return destination

    @staticmethod
    def _csv(path, rows, fields):
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for original in rows:
                row = dict(original)
                for key in fields:
                    source = key[:-5] if key.endswith("_json") else None
                    if source and source in row:
                        row[key] = _json(row[source])
                writer.writerow(row)
        temporary.replace(path)

    def save_recipe(self, name, config):
        if not name.strip() or len(name) > 100:
            raise ValueError("Recipe name must contain 1–100 characters.")
        recipe = {"name": name.strip(), "config": dict(config)}
        digest = hashlib.sha256(name.strip().encode("utf-8")).hexdigest()[:8]
        self._atomic_json(self.recipes_root / f"{_slug(name)}_{digest}.json", recipe)

    def list_recipes(self):
        recipes = []
        for path in self.recipes_root.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value.get("name"), str) and isinstance(value.get("config"), dict):
                    recipes.append(value)
            except (ValueError, OSError, AttributeError):
                continue
        return sorted(recipes, key=lambda item: item["name"].lower())
