"""Small Russian Qt UI; all conversion happens in the shared service on QThread."""

import sys
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QWidget,
)

from coredescriptionw2e.domain import ConversionError, ConversionResult
from coredescriptionw2e.service import convert_batch


class Worker(QThread):
    progress = Signal(int, int, str)
    completed = Signal(int, int, int, str)

    def __init__(self, folder: Path, template: Path, parent: QWidget) -> None:
        super().__init__(parent)
        self.folder = folder
        self.template = template

    def update(self, count: int, total: int, result: ConversionResult) -> None:
        errors = [f"[{i.code}] {i.message}" for i in result.issues]
        self.progress.emit(
            count,
            total,
            f"{result.source.name} ({count}/{total}): {result.status}"
            + ("; " + "; ".join(errors) if errors else ""),
        )

    def run(self) -> None:
        try:
            results = convert_batch(self.folder, self.template, progress=self.update)
            success = sum(r.status == "success" for r in results)
            warnings = sum(i.severity == "warning" for r in results for i in r.issues)
            self.completed.emit(success, len(results) - success, warnings, "")
        except (ConversionError, OSError) as exc:
            self.completed.emit(
                0, 1, 0, str(exc) if isinstance(exc, ConversionError) else "Пути недоступны."
            )
        except Exception:
            # Qt must always deliver completion, even for an unexpected bug.
            self.completed.emit(0, 1, 0, "Внутренняя ошибка; обработка остановлена.")


class Window(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CoreDescriptionW2E")
        self.resize(720, 330)
        self.worker: Worker | None = None
        self.close_after = False
        layout = QGridLayout(self)
        self.folder = QLineEdit()
        self.template = QLineEdit()
        for row, (label, edit, slot) in enumerate(
            (
                ("Папка Word (.doc, .docx)", self.folder, self.choose_folder),
                ("Excel-шаблон (.xlsx)", self.template, self.choose_template),
            )
        ):
            layout.addWidget(QLabel(label), row, 0)
            layout.addWidget(edit, row, 1)
            button = QPushButton("…")
            button.clicked.connect(slot)
            layout.addWidget(button, row, 2)
        self.fill = QPushButton("Заполнить")
        self.exit = QPushButton("Выход")
        self.fill.clicked.connect(self.start)
        self.exit.clicked.connect(self.close)
        layout.addWidget(self.fill, 2, 1)
        layout.addWidget(self.exit, 2, 2)
        self.bar = QProgressBar()
        self.status = QLabel("Выберите папку и шаблон. Результаты: папка Word/output.")
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.bar, 3, 0, 1, 3)
        layout.addWidget(self.status, 4, 0, 1, 3)
        layout.addWidget(self.details, 5, 0, 1, 3)

    @Slot()
    def choose_folder(self) -> None:
        if value := QFileDialog.getExistingDirectory(self, "Папка Word"):
            self.folder.setText(value)

    @Slot()
    def choose_template(self) -> None:
        value, _ = QFileDialog.getOpenFileName(self, "Excel-шаблон", "", "Excel (*.xlsx)")
        if value:
            self.template.setText(value)

    @Slot()
    def start(self) -> None:
        if self.worker is not None:
            return
        if not self.folder.text().strip() or not self.template.text().strip():
            self.status.setText("Выберите папку Word и Excel-шаблон.")
            return
        self.fill.setEnabled(False)
        self.details.clear()
        self.bar.setRange(0, 0)
        self.worker = Worker(Path(self.folder.text()), Path(self.template.text()), self)
        self.worker.progress.connect(self.update_progress)
        self.worker.completed.connect(self.complete)
        self.worker.finished.connect(self.release_worker)
        self.worker.start()

    @Slot(int, int, str)
    def update_progress(self, value: int, total: int, text: str) -> None:
        self.bar.setRange(0, total)
        self.bar.setValue(value)
        self.details.append(text)

    @Slot(int, int, int, str)
    def complete(self, success: int, errors: int, warnings: int, message: str) -> None:
        self.status.setText(
            message or f"Готово: {success}; ошибок файлов: {errors}; предупреждений: {warnings}."
        )
        self.bar.setRange(0, 1)
        self.bar.setValue(1)

    @Slot()
    def release_worker(self) -> None:
        if self.worker:
            self.worker.deleteLater()
        self.worker = None
        self.fill.setEnabled(True)
        if self.close_after:
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None:
            self.close_after = True
            self.status.setText("Обработка завершается; окно закроется после неё.")
            event.ignore()
        else:
            event.accept()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--cli" in args:
        from coredescriptionw2e.cli import main as cli_main

        return cli_main([arg for arg in args if arg != "--cli"])
    if "--frozen-smoke" in args:
        from coredescriptionw2e.smoke import frozen_smoke

        index = args.index("--frozen-smoke")
        return frozen_smoke(Path(args[index + 1])) if index + 1 < len(args) else 2
    app = QApplication([sys.argv[0]])
    window = Window()
    window.show()
    if "--smoke-test" in args:
        QTimer.singleShot(100, window.close)
        QTimer.singleShot(5000, lambda: app.exit(3))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
