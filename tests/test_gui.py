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


def test_gui_review_is_not_counted_as_pass_or_error(sample):
    from coredescriptionw2e.smoke import create_core_docx

    folder, word, template = sample
    create_core_docx(word)
    code = """
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from coredescriptionw2e.gui.app import Window, Worker
app = QApplication([])
window = Window()
worker = Worker(Path(sys.argv[1]), Path(sys.argv[2]), window)
updates, completed = [], []
worker.progress.connect(lambda n, total, text: updates.append(text))
worker.completed.connect(lambda *args: completed.append(args))
worker.run()
assert len(updates) == 1 and 'ТРЕБУЕТ ПРОВЕРКИ' in updates[0]
assert 'review' in updates[0] and 'книга:' in updates[0]
assert completed[0][:3] == (0, 1, 0)
window.complete(*completed[0])
assert 'требует проверки: 1' in window.status.text()
assert 'отказы/пропуски: 0' in window.status.text()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(folder), str(template)],
        capture_output=True,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        timeout=25,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_gui_named_outcomes_match_shared_service(sample):
    folder, _, template = sample
    (folder / "broken.doc").write_bytes(b"invalid")
    (folder / "~$lock.docx").write_bytes(b"temporary")
    code = """
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from coredescriptionw2e.gui.app import Window, Worker, RESULT_RU
from coredescriptionw2e.service import convert_batch, batch_summary
app = QApplication([])
folder, template = Path(sys.argv[1]), Path(sys.argv[2])
expected = convert_batch(folder, template, folder / 'reference')
window = Window()
worker = Worker(folder, template, window)
rows, counters = [], []
worker.file_result.connect(rows.append)
worker.file_result.connect(window.add_result)
worker.batch_results.connect(window.replace_results)
worker.counters.connect(counters.append)
worker.counters.connect(window.show_counters)
worker.completed.connect(window.complete)
worker.run()
assert [(r.source.name, r.status) for r in rows] == [(r.source.name, r.status) for r in expected]
assert counters == [batch_summary(expected)]
assert window.results.rowCount() == 3
for index, r in enumerate(rows):
    assert window.results.item(index, 0).text() == r.source.name
    assert window.results.item(index, 1).text() == RESULT_RU[r.status]
assert 'Всего исходов: 3' in window.status.text()
assert 'discovered=3' in window.details.toPlainText()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(folder), str(template)],
        capture_output=True,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        timeout=25,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
