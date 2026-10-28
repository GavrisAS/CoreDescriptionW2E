"""Small Russian Qt UI; all conversion happens in the shared service on QThread."""

import sys
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QWidget,
)

from coredescriptionw2e import __version__
from coredescriptionw2e.domain import ConversionError, ConversionResult
from coredescriptionw2e.service import batch_summary, convert_batch

RESULT_RU = {
    "success": "Проверено автоматически",
    "review_required": "Требует проверки",
    "unsupported": "Не поддерживается",
    "error": "Ошибка",
    "skipped": "Пропущено с причиной",
}


class Worker(QThread):
    progress = Signal(int, int, str)
    completed = Signal(int, int, int, int, str)
    file_result = Signal(object)
    counters = Signal(object)

    def __init__(self, folder: Path, template: Path, parent: QWidget) -> None:
        super().__init__(parent)
        self.folder = folder
        self.template = template

    def update(self, count: int, total: int, result: ConversionResult) -> None:
        self.file_result.emit(result)
        errors = [f"[{i.code}] {i.message}" for i in result.issues]
        self.progress.emit(
            count,
            total,
            f"{result.source.name} ({count}/{total}): {'ТРЕБУЕТ ПРОВЕРКИ' if result.status == 'review_required' else result.status}; проблем: {len(result.issues)}"
            + (f"; книга: {result.output}" if result.output else "")
            + ("; " + "; ".join(errors) if errors else ""),
        )

    def run(self) -> None:
        try:
            results = convert_batch(self.folder, self.template, progress=self.update)
            self.counters.emit(batch_summary(results))
            success = sum(r.status == "success" for r in results)
            warnings = sum(i.severity == "warning" for r in results for i in r.issues)
            review = sum(r.status == "review_required" for r in results)
            self.completed.emit(success, review, len(results) - success - review, warnings, "")
        except (ConversionError, OSError) as exc:
            self.completed.emit(
                0, 0, 1, 0, str(exc) if isinstance(exc, ConversionError) else "Пути недоступны."
            )
        except Exception:
            # Qt must always deliver completion, even for an unexpected bug.
            self.completed.emit(0, 0, 1, 0, "Внутренняя ошибка; обработка остановлена.")


class Window(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"CoreDescriptionW2E {__version__}")
        self.resize(1050, 640)
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
        self.results = QTableWidget(0, 4)
        self.results.setHorizontalHeaderLabels(
            ["Word-файл", "Исход", "Причина / ограничения", "Результат (двойной щелчок)"]
        )
        self.results.cellDoubleClicked.connect(self.open_result)
        self.open_output = QPushButton("Открыть результаты и приватный batch-отчёт")
        self.open_output.clicked.connect(self.open_folder)
        layout.addWidget(self.bar, 3, 0, 1, 3)
        layout.addWidget(self.status, 4, 0, 1, 3)
        layout.addWidget(self.results, 5, 0, 1, 3)
        layout.addWidget(self.open_output, 6, 0, 1, 3)
        layout.addWidget(self.details, 7, 0, 1, 3)

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
        self.results.setRowCount(0)
        self.bar.setRange(0, 0)
        self.worker = Worker(Path(self.folder.text()), Path(self.template.text()), self)
        self.worker.progress.connect(self.update_progress)
        self.worker.file_result.connect(self.add_result)
        self.worker.counters.connect(self.show_counters)
        self.worker.completed.connect(self.complete)
        self.worker.finished.connect(self.release_worker)
        self.worker.start()

    @Slot(object)
    def add_result(self, result: ConversionResult) -> None:
        row = self.results.rowCount()
        self.results.insertRow(row)
        for column, text in enumerate(
            (
                result.source.name,
                RESULT_RU[result.status],
                "; ".join(dict.fromkeys(i.message for i in result.issues)),
                str(result.output) if result.output else "",
            )
        ):
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            self.results.setItem(row, column, item)
        self.results.setColumnWidth(0, 220)
        self.results.setColumnWidth(1, 180)
        self.results.setColumnWidth(2, 360)

    @Slot(object)
    def show_counters(self, counts: dict[str, int]) -> None:
        self.details.append("; ".join(f"{key}={value}" for key, value in counts.items()))

    @Slot(int, int)
    def open_result(self, row: int, column: int) -> None:
        item = self.results.item(row, 3)
        if item and item.text():
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.text()))

    @Slot()
    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.folder.text()) / "output")))

    @Slot(int, int, str)
    def update_progress(self, value: int, total: int, text: str) -> None:
        self.bar.setRange(0, total)
        self.bar.setValue(value)
        self.details.append(text)

    @Slot(int, int, int, int, str)
    def complete(self, success: int, review: int, errors: int, warnings: int, message: str) -> None:
        self.status.setText(
            message
            or f"Всего исходов: {success + review + errors}; проверено: {success}; требует проверки: {review}; отказы/пропуски: {errors}; предупреждений: {warnings}."
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
    if args == ["--version"]:
        from coredescriptionw2e.cli import main as cli_main

        return cli_main(args)
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
