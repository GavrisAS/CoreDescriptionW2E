import os
import subprocess
import sys

from coredescriptionw2e.smoke import frozen_smoke


def test_real_doc_docx_smoke(tmp_path):
    assert frozen_smoke(tmp_path / "Чистый каталог с пробелами") == 0


def test_gui_smoke():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, "-m", "coredescriptionw2e.gui.app", "--smoke-test"],
        env=env,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, "-m", "coredescriptionw2e", "--help"], capture_output=True, timeout=20
    )
    assert result.returncode == 0 and b"--input-dir" in result.stdout
