from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))


class BrandLabTestCase(unittest.TestCase):
    def run_cli(self, *args: str, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
        process = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / args[0]), *args[1:]],
            cwd=SKILL_ROOT, text=True, capture_output=True,
        )
        if expect_success and process.returncode != 0:
            self.fail(f"Command failed: {args}\nSTDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}")
        return process

    def init_workspace(self, workspace: Path) -> None:
        self.run_cli(
            "init_workspace.py", "--workspace", str(workspace), "--brand", "Miss Sixty",
            "--mode", "pure", "--source-language", "en", "--working-language", "zh-CN",
            "--target-market", "IT:it-IT",
        )

    def temp_workspace(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="brandlab-test-", dir="/tmp")) / "brandlab"
