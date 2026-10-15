import csv
import io
import json
from pathlib import Path

from base import BrandLabTestCase


class ImportExportHardeningTest(BrandLabTestCase):
    def write_model_translations(self, workspace: Path, job_id: str, translations: dict[str, str]) -> None:
        self.run_cli("translate_job.py", "--workspace", str(workspace), "--job", job_id)
        job = workspace / "jobs" / job_id
        worklist = [json.loads(line) for line in (job / "worklist.jsonl").read_text(encoding="utf-8").splitlines()]
        rows = []
        for row in worklist:
            resource = row["resource_type"]
            if resource not in translations:
                raise AssertionError(f"Unexpected resource type in job: {resource}")
            rows.append({
                "item_id": row["item_id"],
                "translated_text": translations[resource],
                "source_digest": row["source_digest"],
                "source_locale": "en",
                "target_locale": "it-IT",
            })
        (job / "model-translations.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
        )
        self.run_cli("translate_job.py", "--workspace", str(workspace), "--job", job_id, "--input", "model-translations.jsonl")
        self.run_cli("build_review.py", "--workspace", str(workspace), "--job", job_id)
        self.run_cli(
            "apply_approval.py", "--workspace", str(workspace), "--job", job_id,
            "--decision", "approve", "--item-id", "all", "--reviewer", "Jayce",
        )

    def approved_job(self, workspace: Path, job_id: str, translations: dict[str, str]) -> None:
        self.run_cli("create_job.py", "--workspace", str(workspace), "--target-locale", "it-IT", "--job-id", job_id)
        self.write_model_translations(workspace, job_id, translations)

    def read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_shopify_csv_bom_multiline_html_round_trip(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        source_csv = """Handle,Title,Body (HTML),SEO Title,SEO Description
golden-glow,"Golden Glow Dress","<p>Golden Glow Dress</p>
<p>Second line {{size_note}}</p>","Golden Glow Dress","Golden glow dress for evening looks."
"""
        (workspace / "inbox" / "bom-products.csv").write_text(source_csv, encoding="utf-8-sig")
        self.run_cli("import_content.py", "--workspace", str(workspace), "--source-locale", "en")
        self.approved_job(workspace, "bom-job", {
            "product_title": "Abito Golden Glow",
            "product_description": "<p>Abito Golden Glow</p>\n<p>Seconda riga {{size_note}}</p>",
            "seo_title": "Abito Golden Glow",
            "seo_description": "Abito Golden Glow per la sera.",
        })
        output = workspace / "exports" / "bom.csv"
        self.run_cli(
            "build_export.py", "--workspace", str(workspace), "--job", "bom-job",
            "--format", "shopify_csv", "--output", "bom.csv",
        )
        self.assertTrue(output.exists())
        self.assertTrue(output.read_bytes().startswith(b"Handle"))
        rows = self.read_csv(output)
        self.assertEqual(len(rows), 1)
        expected = "<p>Abito Golden Glow</p>\n<p>Seconda riga {{size_note}}</p>"
        self.assertEqual(rows[0]["Body (HTML)"], expected)
        self.assertEqual(rows[0]["Title"], "Abito Golden Glow")
        self.assertEqual(rows[0]["SEO Description"], "Abito Golden Glow per la sera.")
        self.assertEqual(rows[0]["Handle"], "golden-glow")

    def test_generic_csv_round_trip_covers_key_resource_types(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        types = [
            "product_title", "product_description", "seo_title", "seo_description",
            "page_title", "page_body", "blog_title", "blog_body", "care_and_compliance",
        ]
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=["Handle", "Resource Type", "Translation"], lineterminator="\n")
        writer.writeheader()
        for resource in types:
            writer.writerow({"Handle": f"generic-{resource.replace('_', '-')}", "Resource Type": resource, "Translation": f"Golden glow {resource.replace('_', ' ')}."})
        (workspace / "inbox" / "generic.csv").write_text(buffer.getvalue(), encoding="utf-8")
        self.run_cli("import_content.py", "--workspace", str(workspace), "--source-locale", "en")
        translations = {resource: f"Contenuto {resource.replace('_', ' ')}." for resource in types}
        self.approved_job(workspace, "generic-job", translations)
        output = workspace / "exports" / "generic.csv"
        self.run_cli("build_export.py", "--workspace", str(workspace), "--job", "generic-job", "--format", "csv", "--output", "generic.csv")
        rows = self.read_csv(output)
        self.assertEqual(len(rows), len(types))
        by_type = {row["Resource Type"]: row for row in rows}
        for resource in types:
            self.assertEqual(by_type[resource]["Translation"], translations[resource])
            self.assertEqual(by_type[resource]["Handle"], f"generic-{resource.replace('_', '-')}")

    def test_custom_field_mapping_controls_import_and_export_headers(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        (workspace / "field-mappings.yaml").write_text(
            "import_csv:\n  Titolo: product_title\n  Descrizione: product_description\n"
            "export_shopify:\n  product_title: Titolo Prodotto\n  product_description: Descrizione Prodotto\n",
            encoding="utf-8",
        )
        (workspace / "inbox" / "custom-products.csv").write_text(
            "Handle,Titolo,Descrizione\ncustom-glow,\"Custom Glow Dress\",\"<p>Custom glow dress.</p>\"\n",
            encoding="utf-8",
        )
        self.run_cli("import_content.py", "--workspace", str(workspace), "--source-locale", "en")
        self.approved_job(workspace, "custom-job", {
            "product_title": "Abito Custom Glow",
            "product_description": "<p>Abito Custom Glow.</p>",
        })
        output = workspace / "exports" / "custom.csv"
        self.run_cli(
            "build_export.py", "--workspace", str(workspace), "--job", "custom-job",
            "--format", "shopify_csv", "--output", "custom.csv",
        )
        rows = self.read_csv(output)
        self.assertEqual(rows[0]["Titolo Prodotto"], "Abito Custom Glow")
        self.assertEqual(rows[0]["Descrizione Prodotto"], "<p>Abito Custom Glow.</p>")
        with output.open("r", encoding="utf-8", newline="") as handle:
            header = handle.readline().strip()
            self.assertTrue(header.startswith("Handle,Titolo Prodotto,Descrizione Prodotto,"))

    def test_invalid_utf8_csv_has_actionable_error(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        path = workspace / "inbox" / "invalid.csv"
        path.write_bytes(b"Handle,Title\n\xff\xfe,Broken\n")
        process = self.run_cli(
            "import_content.py", "--workspace", str(workspace), "--source-locale", "en",
            expect_success=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("file is not valid UTF-8", process.stderr)
        self.assertTrue((workspace / "content" / "catalog.jsonl").exists())

    def test_shopify_export_rejects_missing_handle_before_output(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        (workspace / "inbox" / "missing-handle.json").write_text(
            json.dumps([{"external_id": "", "resource_type": "product_title", "source": "Golden Glow Dress"}]) + "\n",
            encoding="utf-8",
        )
        self.run_cli("import_content.py", "--workspace", str(workspace), "--source-locale", "en")
        self.approved_job(workspace, "missing-handle-job", {"product_title": "Abito Golden Glow"})
        process = self.run_cli(
            "build_export.py", "--workspace", str(workspace), "--job", "missing-handle-job",
            "--format", "shopify_csv", "--output", "missing-handle.csv", expect_success=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("Handle is missing", process.stderr)
        self.assertFalse((workspace / "exports" / "missing-handle.csv").exists())

    def test_shopify_export_rejects_unsupported_resource_before_output(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        (workspace / "inbox" / "page.json").write_text(
            json.dumps([{"external_id": "about", "resource_type": "page_body", "source": "Golden glow page."}]) + "\n",
            encoding="utf-8",
        )
        self.run_cli("import_content.py", "--workspace", str(workspace), "--source-locale", "en")
        self.approved_job(workspace, "unsupported-job", {"page_body": "Pagina Golden Glow."})
        process = self.run_cli(
            "build_export.py", "--workspace", str(workspace), "--job", "unsupported-job",
            "--format", "shopify_csv", "--output", "unsupported.csv", expect_success=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("is not exportable as Shopify product CSV", process.stderr)
        self.assertFalse((workspace / "exports" / "unsupported.csv").exists())
