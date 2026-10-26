"""Bounded offline readers retain raw text independently of normalization."""

import re
import unicodedata
from pathlib import Path

from coredescriptionw2e.domain import ConversionError, ImportedDocument, SourceRef, TextBlock

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TEXT_CHARS = 8 * 1024 * 1024
MAX_BLOCKS = 100_000


def check_file(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ConversionError("Источник не является обычным доступным файлом.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ConversionError("Размер документа превышает лимит 64 МиБ.")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\u00a0", " ")
    # Preserve every number, dash and paragraph boundary; never dehyphenate words.
    return re.sub(r"[ \t]+", " ", text).strip()


def make_document(path: Path, entries: list[tuple[str, str]], raw: str) -> ImportedDocument:
    if len(raw) > MAX_TEXT_CHARS or len(entries) > MAX_BLOCKS:
        raise ConversionError("Превышен лимит текста или количества блоков.")
    blocks = [
        TextBlock(value, normalize(value), SourceRef(path.name, position, i))
        for i, (position, value) in enumerate(entries)
        if normalize(value)
    ]
    return ImportedDocument(path.name, raw, blocks)


def read_word(path: Path) -> ImportedDocument:
    check_file(path)
    if path.suffix.lower() == ".doc":
        from .doc_reader import read_doc

        return read_doc(path)
    if path.suffix.lower() == ".docx":
        from .docx_reader import read_docx

        return read_docx(path)
    raise ConversionError("Поддерживаются только DOC и DOCX.")
