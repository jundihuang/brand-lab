from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from base import BrandLabTestCase
from brandlab_lib import (
    BrandLabError, append_history, atomic_write_text, load_brand, load_job,
    resolve_job_path,
    save_job, write_catalog,
)
from mutation_lib import save_plan
from validation_lib import schema_errors


class SchemaValidationTest(BrandLabTestCase):
    def test_invalid_brand_fails_with_file_field_and_repair_hint(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        path = workspace / "brand.yaml"
        value = path.read_text(encoding="utf-8").replace("mode: pure", "mode: unsupported")
        path.write_text(value, encoding="utf-8")
        with self.assertRaises(BrandLabError) as ctx:
            load_brand(workspace)
        message = str(ctx.exception)
        self.assertIn("[SCHEMA_INVALID]", message)
        self.assertIn("brand.yaml", message)
        self.assertIn("$.mode", message)
        self.assertIn("Fix:", message)

    def test_invalid_catalog_row_is_rejected_before_write(self) -> None:
        workspace = self.temp_workspace()
        path = workspace / "content" / "catalog.jsonl"
        path.parent.mkdir(parents=True)
        valid = {
            "id": "item", "resource_type": "product_title", "source_locale": "en",
            "target_locale": "it-IT", "source": "Skirt", "digest": "sha256:" + "0" * 64,
            "status": "new",
        }
        invalid = {**valid, "id": ""}
        with self.assertRaises(BrandLabError) as ctx:
            write_catalog(workspace, [valid, invalid])
        self.assertIn("[SCHEMA_INVALID]", str(ctx.exception))
        self.assertIn("catalog.jsonl:2", str(ctx.exception))
        self.assertFalse(path.exists())

    def test_invalid_job_manifest_is_rejected_on_load_and_save(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        job_dir = workspace / "jobs" / "bad-job"
        job_dir.mkdir(parents=True)
        manifest = {
            "job_id": "bad-job", "status": "unsupported", "mode": "pure",
            "source_locale": "en", "target_locale": "it-IT", "items": [],
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(BrandLabError) as loaded:
            load_job(workspace, "bad-job")
        self.assertIn("[SCHEMA_INVALID]", str(loaded.exception))
        with self.assertRaises(BrandLabError):
            save_job(job_dir, manifest)
        self.assertEqual(json.loads((job_dir / "manifest.json").read_text())["status"], "unsupported")

    def test_invalid_mutation_plan_is_rejected_before_save(self) -> None:
        path = Path("/tmp/brandlab-invalid-plan.json")
        plan = {"batch_id": "batch", "status": "draft"}
        with self.assertRaises(BrandLabError) as ctx:
            save_plan(path, plan)
        self.assertIn("[SCHEMA_INVALID]", str(ctx.exception))
        self.assertFalse(path.exists())
        self.assertTrue(schema_errors("mutation-plan", plan))

    def test_invalid_approval_state_is_rejected(self) -> None:
        value = {"decisions": [{"item_id": "", "decision": "unknown"}]}
        self.assertTrue(any("decisions[0]" in error for error in schema_errors("approval-state", value)))

    def test_invalid_history_event_fails_without_append(self) -> None:
        workspace = self.temp_workspace()
        history = workspace / "history" / "events.jsonl"
        history.parent.mkdir(parents=True)
        history.write_text('{"valid":true}\n', encoding="utf-8")
        with self.assertRaises(BrandLabError):
            append_history(workspace, "", {})
        self.assertEqual(history.read_text(encoding="utf-8"), '{"valid":true}\n')

    def test_job_path_traversal_is_rejected(self) -> None:
        job_dir = Path("/tmp/brandlab-job")
        with self.assertRaises(BrandLabError) as ctx:
            resolve_job_path(job_dir, "../escape.json")
        self.assertIn("[PATH_INVALID]", str(ctx.exception))
        self.assertIn("escapes", str(ctx.exception))

    def test_atomic_text_write_failure_preserves_original_file(self) -> None:
        path = Path("/tmp/brandlab-atomic.json")
        atomic_write_text(path, "original\n")
        try:
            with mock.patch("os.replace", side_effect=OSError("replace blocked")):
                with self.assertRaises(OSError):
                    atomic_write_text(path, "replacement\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(list(path.parent.glob(path.name + ".*")), [])
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
