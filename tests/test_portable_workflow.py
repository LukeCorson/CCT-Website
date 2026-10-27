"""Profile immutability, private support bundles, and finished-record import."""

import json
import hashlib
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cct.contracts import DEFAULT_CONFIG
from cct.portable import export_support_bundle, import_completed_run, save_apparatus_profile
from cct.storage import RunStore


class PortableWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def make_run(self, status="complete"):
        store = RunStore(self.root / "source")
        metadata = store.create(dict(DEFAULT_CONFIG), "simulation")
        store.append_sample({"run_id": metadata["id"], "elapsed_s": 1, "stage": "breakthrough", "cycle": 1, "co2_ppm": 42.0})
        store.append_event({"run_id": metadata["id"], "event": "run_finished", "message": "Finished"})
        if status == "running":
            store.close()
        else:
            store.finalize(status, 1, summary={"cycles": [{"cycle": 1, "status": "complete"}]})
        return Path(metadata["path"]), metadata

    def test_profile_saves_unique_copies_and_preserves_flags(self):
        profile = {"commissioned": True, "devices": {"core2": {"port": "COM9"}}}
        one = save_apparatus_profile(self.root, profile)
        two = save_apparatus_profile(self.root, profile)
        self.assertNotEqual(one, two)
        self.assertEqual(json.loads(one.read_text(encoding="utf-8")), profile)
        profile["devices"]["core2"]["port"] = "COM10"
        self.assertEqual(json.loads(one.read_text(encoding="utf-8"))["devices"]["core2"]["port"], "COM9")
        self.assertFalse(any(one.parent.glob(".writing_*")))

    def test_profiles_reject_nonobject_nonfinite_and_oversized_data(self):
        for profile in ([1, 2], {"value": float("nan")}, {"value": object()}, {"value": "x" * (1024 * 1024)}):
            with self.subTest(profile_type=type(profile).__name__), self.assertRaises(ValueError):
                save_apparatus_profile(self.root, profile)

    def test_profile_name_collision_refuses_overwrite(self):
        with patch("cct.portable._token", return_value="collision"):
            first = save_apparatus_profile(self.root, {"commissioned": True})
            with self.assertRaises(FileExistsError):
                save_apparatus_profile(self.root, {"commissioned": False})
        self.assertTrue(json.loads(first.read_text(encoding="utf-8"))["commissioned"])
        self.assertFalse(any(first.parent.glob(".writing_*")))

    def test_support_contains_readiness_without_private_data(self):
        private = "PRIVATE_OPERATOR_SAMPLE_SERIAL_123"
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "app.log").write_text(private, encoding="utf-8")
        snapshot = {"mode": "hardware", "connected": True, "running": False, "stage": private, "outcome": "fault",
                    "cycle": 1, "operator": private, "sample_id": private, "run_path": "C:/Private/" + private,
                    "values": {"co2_ppm": 123.456}, "series": [{"co2_ppm": 123.456}],
                    "events": [{"message": private}], "blockers": ["Profile C:/Private/" + private],
                    "outputs": {"route": private, "gas": private, "command_fault": private, "pending_count": 1},
                    "devices": {"core2": {"connected": True, "fresh": False, "message": private, "serial": private}}}
        destination = export_support_bundle(self.root, snapshot)
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(set(archive.namelist()), {"diagnostics.json", "README.txt"})
            content = b"\n".join(archive.read(name) for name in archive.namelist())
            self.assertNotIn(private.encode(), content)
            self.assertNotIn(b"123.456", content)
            self.assertNotIn(str(self.root).encode(), content)
            report = json.loads(archive.read("diagnostics.json"))
            self.assertEqual(report["state"]["readiness"]["blocker_codes"], ["profile_configuration"])
            self.assertFalse(report["privacy"]["raw_logs_included"])
            self.assertEqual(report["state"]["stage"], "unknown")
            self.assertIn("PySide6", report["dependencies"])

    def test_finished_import_preserves_values_and_regenerates_frozen_files(self):
        source, metadata = self.make_run()
        source_hash = hashlib.sha256((source / "run.sqlite").read_bytes()).hexdigest()
        (source / "unexpected.txt").write_text("DO NOT COPY", encoding="utf-8")
        destination_root = self.root / "destination"
        run_id = import_completed_run(destination_root, source)
        self.assertNotEqual(run_id, metadata["id"])
        loaded = RunStore(destination_root).load(run_id)
        self.assertEqual(loaded["metadata"]["status"], "complete")
        self.assertEqual(loaded["metadata"]["source_import"]["original_id"], metadata["id"])
        self.assertEqual(loaded["samples"][0]["co2_ppm"], 42)
        self.assertEqual(loaded["samples"][0]["run_id"], run_id)
        self.assertEqual(loaded["samples"][0]["source_run_id"], metadata["id"])
        folder = destination_root / "runs" / run_id
        self.assertEqual(loaded["metadata"]["path"], str(folder))
        self.assertEqual(set(path.name for path in folder.iterdir()), {"run.sqlite", "metadata.json", "recipe.json", "test_settings.json", "summary.json"})
        self.assertEqual(json.loads((folder / "test_settings.json").read_text(encoding="utf-8")), DEFAULT_CONFIG)
        self.assertTrue(RunStore(destination_root).export(run_id).is_dir())
        self.assertEqual(hashlib.sha256((source / "run.sqlite").read_bytes()).hexdigest(), source_hash)

    def test_repeated_imports_never_overwrite_existing_records(self):
        source, _ = self.make_run("aborted")
        destination = self.root / "destination"
        first = import_completed_run(destination, source)
        second = import_completed_run(destination, source)
        self.assertNotEqual(first, second)
        self.assertEqual(len(RunStore(destination).list_runs()), 2)

    def test_running_and_active_wal_records_are_rejected(self):
        source, _ = self.make_run("running")
        with self.assertRaisesRegex(ValueError, "Only finished"):
            import_completed_run(self.root / "destination", source)
        completed, _ = self.make_run()
        (completed / "run.sqlite-wal").write_bytes(b"ACTIVE")
        with self.assertRaisesRegex(ValueError, "journal"):
            import_completed_run(self.root / "destination", completed)

    def test_identity_mismatch_and_unknown_schema_are_rejected(self):
        source, _ = self.make_run()
        renamed = source.with_name("renamed-record")
        source.rename(renamed)
        with self.assertRaisesRegex(ValueError, "folder"):
            import_completed_run(self.root / "destination", renamed)
        source, _ = self.make_run()
        with closing(sqlite3.connect(source / "run.sqlite")) as database:
            database.execute("CREATE TABLE unexpected(secret TEXT)")
            database.commit()
        with self.assertRaisesRegex(ValueError, "unsupported"):
            import_completed_run(self.root / "destination", source)

    def test_failed_import_removes_its_partial_folder_only(self):
        source, _ = self.make_run()
        destination_root = self.root / "destination"
        existing = destination_root / "runs" / "keep-me"
        existing.mkdir(parents=True)
        (existing / "keep.txt").write_text("keep", encoding="utf-8")
        with patch("cct.portable._json_bytes", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                import_completed_run(destination_root, source)
        self.assertEqual([path.name for path in (destination_root / "runs").iterdir()], ["keep-me"])
        self.assertEqual((existing / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_import_rejects_malformed_measurements_before_history_load(self):
        source, _ = self.make_run()
        with closing(sqlite3.connect(source / "run.sqlite")) as database:
            database.execute("UPDATE samples SET data=?", (json.dumps({"elapsed_s": 1, "stage": "breakthrough", "cycle": "not-an-integer", "co2_ppm": "invalid"}),))
            database.commit()
        with self.assertRaisesRegex(ValueError, "cycle"):
            import_completed_run(self.root / "destination", source)

    def test_import_rejects_sql_triggers_without_executing_them(self):
        source, _ = self.make_run()
        with closing(sqlite3.connect(source / "run.sqlite")) as database:
            database.execute("CREATE TRIGGER unwanted AFTER UPDATE ON metadata BEGIN DELETE FROM samples; END")
            database.commit()
        before = hashlib.sha256((source / "run.sqlite").read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "unsupported"):
            import_completed_run(self.root / "destination", source)
        self.assertEqual(hashlib.sha256((source / "run.sqlite").read_bytes()).hexdigest(), before)

    def test_import_rejects_malformed_frozen_test_settings(self):
        for invalid in ({"test_type": []}, {"breakthrough_pct": "invalid"},
                        {"test_type": "zero", "automatic_cutoff_enabled": False, "breakthrough_pct": "invalid"}):
            with self.subTest(invalid=invalid):
                source, _ = self.make_run()
                with closing(sqlite3.connect(source / "run.sqlite")) as database:
                    database.execute("UPDATE metadata SET value=? WHERE key='recipe'", (json.dumps({**DEFAULT_CONFIG, **invalid}),))
                    database.commit()
                with self.assertRaisesRegex(ValueError, "frozen test settings"):
                    import_completed_run(self.root / "destination", source)

    def test_import_rejects_malformed_event_plot_fields(self):
        for invalid in ({"elapsed_s": "not-a-duration"}, {"stage_elapsed_s": -2}, {"cycle": "first"}):
            with self.subTest(invalid=invalid):
                source, _ = self.make_run()
                with closing(sqlite3.connect(source / "run.sqlite")) as database:
                    database.execute("UPDATE events SET data=?", (json.dumps({"event": "marker", "message": "marker", **invalid}),))
                    database.commit()
                with self.assertRaisesRegex(ValueError, "Imported events"):
                    import_completed_run(self.root / "destination", source)

    def test_import_rejects_malformed_summary_render_fields(self):
        for invalid in ({"measurement_duration_s": "invalid"}, {"cycles": [{"cycle": 1, "threshold_ratio": "invalid"}]},
                        {"cycles": [{"cycle": "first", "status": "complete"}]}, {"cycles": [{"cycle": 1, "status": []}]}):
            with self.subTest(invalid=invalid):
                source, _ = self.make_run()
                with closing(sqlite3.connect(source / "run.sqlite")) as database:
                    database.execute("UPDATE metadata SET value=? WHERE key='summary'", (json.dumps(invalid),))
                    database.commit()
                with self.assertRaisesRegex(ValueError, "metadata|summary"):
                    import_completed_run(self.root / "destination", source)


if __name__ == "__main__":
    unittest.main()
