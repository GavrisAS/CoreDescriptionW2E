import os
import subprocess
import sys


def test_gui_worker_repeated_start_and_deferred_close(sample):
    folder, _, template = sample
    code = """
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from coredescriptionw2e.gui.app import Window
from coredescriptionw2e.smoke import verify_output
from pathlib import Path
app = QApplication([])
window = Window()
window.folder.setText(sys.argv[1])
window.template.setText(sys.argv[2])
window.show()
window.start()
first = window.worker
window.start()
assert window.worker is first
assert not window.fill.isEnabled()
window.close()
assert window.close_after and window.isVisible()
QTimer.singleShot(15000, lambda: app.exit(3))
status = app.exec()
assert status == 0
assert window.worker is None
verify_output(Path(sys.argv[1]) / 'output' / 'Учебный.xlsx')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(folder), str(template)],
        capture_output=True,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        timeout=25,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
