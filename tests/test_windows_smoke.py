import os
import subprocess
import sys

import pytest
from openpyxl import load_workbook

from coredescriptionw2e.service import convert_batch
from coredescriptionw2e.smoke import frozen_smoke, verify_output


def test_real_doc_docx_smoke(tmp_path):
    assert frozen_smoke(tmp_path / "Чистый каталог с пробелами") == 0


@pytest.mark.parametrize("cell,value", [("J3", None), ("K3", None), ("L3", None), ("J4", 3)])
def test_smoke_rejects_missing_repeated_metrics_and_boundary_leak(sample, cell, value):
    folder, _, template = sample
    result = convert_batch(folder, template)[0]
    assert result.output is not None
    workbook = load_workbook(result.output)
    workbook.active[cell] = value
    workbook.save(result.output)
    workbook.close()
    with pytest.raises(AssertionError):
        verify_output(result.output)


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
