"""Word 97–2003 main story, using MS-DOC FIB/CLX/PlcPcd (not byte scanning).

Only OLE and the piece table are interpreted. VBA, fields, images and links are
never executed. Word 6/95, encryption and text boxes fail closed.
"""

import re
import struct
from pathlib import Path

import olefile

from coredescriptionw2e.domain import ConversionError, ImportedDocument, ParseIssue

from .common import MAX_FILE_BYTES, MAX_TEXT_CHARS, check_file, make_document


def _slice(data: bytes, offset: int, length: int) -> bytes:
    if offset < 0 or length < 0 or offset + length > len(data):
        raise ConversionError("DOC: структура выходит за границы потока.")
    return data[offset : offset + length]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack("<H", _slice(data, offset, 2))[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack("<I", _slice(data, offset, 4))[0]


def extract_main_story(word: bytes, table: bytes) -> str:
    if _u16(word, 0) != 0xA5EC or _u16(word, 2) not in {0xC1, 0xD9, 0x101, 0x10C, 0x112}:
        raise ConversionError("DOC: поддерживается бинарный Word 97–2003, не Word 6/95.")
    flags = _u16(word, 10)
    if flags & (0x0100 | 0x8000):
        raise ConversionError("DOC: зашифрованные документы не поддерживаются.")
    pos = 32
    pos += 2 + 2 * _u16(word, pos)
    long_count = _u16(word, pos)
    pos += 2
    if long_count < 11:
        raise ConversionError("DOC: неполный FIB.")
    main_length = _u32(word, pos + 3 * 4)
    if main_length > MAX_TEXT_CHARS:
        raise ConversionError("DOC: превышен лимит основного текста.")
    if _u32(word, pos + 9 * 4):
        raise ConversionError("DOC: текстовые рамки требуют ручной проверки; импорт остановлен.")
    cb_mac = _u32(word, pos)
    if cb_mac > len(word):
        raise ConversionError("DOC: повреждён размер WordDocument.")
    word = word[:cb_mac]
    pos += long_count * 4
    pair_count = _u16(word, pos)
    pos += 2
    if pair_count <= 33:
        raise ConversionError("DOC: отсутствует таблица фрагментов.")
    clx = _slice(table, _u32(word, pos + 33 * 8), _u32(word, pos + 33 * 8 + 4))
    pos = 0
    while pos < len(clx) and clx[pos] == 1:
        pos += 3 + _u16(clx, pos + 1)
    if pos >= len(clx) or clx[pos] != 2:
        raise ConversionError("DOC: отсутствует Pcdt; эвристическое извлечение запрещено.")
    plc = _slice(clx, pos + 5, _u32(clx, pos + 1))
    if len(plc) < 16 or (len(plc) - 4) % 12:
        raise ConversionError("DOC: повреждён PlcPcd.")
    count = (len(plc) - 4) // 12
    if count > 100_000:
        raise ConversionError("DOC: слишком много фрагментов.")
    cps = [_u32(plc, i * 4) for i in range(count + 1)]
    if (
        cps[0] != 0
        or cps[-1] < main_length
        or any(a >= b for a, b in zip(cps, cps[1:], strict=False))
    ):
        raise ConversionError("DOC: нарушен порядок символьных позиций.")
    chunks: list[bytes] = []
    for i in range(count):
        if cps[i] >= main_length:
            break
        length = min(cps[i + 1], main_length) - cps[i]
        fc = _u32(plc, 4 * (count + 1) + i * 8 + 2)
        offset = fc & 0x3FFFFFFF
        if fc & 0x40000000:
            part = _slice(word, offset // 2, length)
            # MS-DOC FcCompressed: identity mapping except defined CP1252 slots.
            text = "".join(
                bytes([b]).decode("cp1252") if b in _CP1252_SPECIAL else chr(b) for b in part
            )
            chunks.append(text.encode("utf-16-le"))
        else:
            chunks.append(_slice(word, offset, length * 2))
    try:
        return b"".join(chunks).decode("utf-16-le", errors="strict")
    except UnicodeError as exc:
        raise ConversionError("DOC: некорректный Unicode в тексте.") from exc


_CP1252_SPECIAL = set(range(0x82, 0x8D)) | set(range(0x91, 0x9D)) | {0x8E, 0x9E, 0x9F}
# 0x8E/0x9E are NOT remapped by MS-DOC (unlike CP1252).
_CP1252_SPECIAL -= {0x8E, 0x9E}


def read_doc(path: Path) -> ImportedDocument:
    check_file(path)
    try:
        with olefile.OleFileIO(path, raise_defects=olefile.DEFECT_INCORRECT) as ole:
            if ole.get_size("WordDocument") > MAX_FILE_BYTES:
                raise ConversionError("DOC: слишком большой поток.")
            word = ole.openstream("WordDocument").read()
            name = "1Table" if _u16(word, 10) & 0x0200 else "0Table"
            if ole.get_size(name) > MAX_FILE_BYTES:
                raise ConversionError("DOC: слишком большая таблица.")
            raw = extract_main_story(word, ole.openstream(name).read())
    except (OSError, ValueError, IndexError, struct.error) as exc:
        raise ConversionError("DOC: повреждён или недоступен OLE-контейнер.") from exc
    # Field instructions are not geological text. Reject instead of mixing them
    # with the displayed field result or silently removing source characters.
    if any(c in raw for c in "\x13\x14\x15"):
        raise ConversionError(
            "DOC: поля Word требуют предварительного сохранения как обычного текста."
        )
    entries = []
    for match in re.finditer(r"[^\r\x07\x0b\x0c]+", raw):
        entries.append((f"cp:{match.start()}-{match.end()}", match.group()))
    result = make_document(path, entries, raw)
    result.issues.append(
        ParseIssue(
            "doc_main_story",
            "DOC: прочитан основной текст; оформление и колонтитулы не импортируются.",
        )
    )
    return result
