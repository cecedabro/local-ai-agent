"""cv-gpu-swap with stub docker/systemctl on PATH: flags, the pre-check, and the hand-paused case."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SWAP = Path(__file__).resolve().parent.parent / "cv-gpu-swap"
DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$LOG"
if [[ " $* " == *" config --images "* ]]; then echo stack/api:1; echo ollama/ollama:1; fi
if [[ "$1" == image && "$2" == inspect && -e "$MISSING" && "$3" == ollama/ollama:1 ]]; then exit 1; fi
exit 0
"""
SYSTEMCTL = """#!/usr/bin/env bash
echo "systemctl $*" >> "$LOG"
"""


class SwapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.d = Path(tempfile.mkdtemp())
        (self.d / "bin").mkdir()
        for name, text in (("docker", DOCKER), ("systemctl", SYSTEMCTL)):
            (self.d / "bin" / name).write_text(text)
            (self.d / "bin" / name).chmod(0o755)
        (self.d / "stack").mkdir()
        (self.d / "swap.env").write_text(f"STACK_DIR={self.d / 'stack'}\n")
        self.flags = self.d / "flags"
        self.log = self.d / "log"
        self.env = dict(os.environ, PATH=f"{self.d / 'bin'}:{os.environ['PATH']}", LOG=str(self.log),
                        MISSING=str(self.d / "missing"), CV_GATE_SWAP_ENV=str(self.d / "swap.env"),
                        CV_GATE_FLAGS=str(self.flags))

    def swap(self, verb: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SWAP), verb], env=self.env, capture_output=True, text=True)

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def test_in_then_out(self) -> None:
        self.assertEqual(self.swap("in").returncode, 0)
        self.assertTrue((self.flags / "paused").exists() and (self.flags / "busy").exists())
        self.assertIn("systemctl stop llama-server", self.calls())
        self.assertIn("-p cv-skills", self.calls())
        self.assertIn("compose.gpu.yml", self.calls())
        self.assertIn("up -d", self.calls())
        self.assertEqual(self.swap("out").returncode, 0)
        self.assertFalse((self.flags / "paused").exists())
        self.assertTrue((self.flags / "busy").exists())          # left to expire, never clobbers a job's flag
        self.assertIn("systemctl start llama-server", self.calls())

    def test_missing_image_fails_fast_and_touches_nothing(self) -> None:
        (self.d / "missing").touch()
        res = self.swap("in")
        self.assertEqual(res.returncode, 3)
        self.assertIn("images not pulled: ollama/ollama:1", res.stderr)
        self.assertNotIn("systemctl", self.calls())
        self.assertFalse((self.flags / "paused").exists())

    def test_paused_by_hand_stays_paused(self) -> None:
        self.flags.mkdir()
        (self.flags / "paused").touch()
        self.swap("in")
        self.swap("out")
        self.assertTrue((self.flags / "paused").exists())
        self.assertNotIn("systemctl start", self.calls())

    def test_unknown_verb(self) -> None:
        self.assertEqual(self.swap("rm -rf").returncode, 2)


if __name__ == "__main__":
    unittest.main()
