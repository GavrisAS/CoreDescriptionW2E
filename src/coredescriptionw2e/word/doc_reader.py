"""Word 97–2003 main story, using MS-DOC FIB/CLX/PlcPcd (not byte scanning).

Only OLE and the piece table are interpreted. VBA, fields, images and links are
never executed. Word 6/95, encryption and nonempty text boxes fail closed.
"""

import re
import struct
from bisect import bisect_right
from dataclasses import replace
from pathlib import Path

import olefile

from coredescriptionw2e.domain import ConversionError, ImportedDocument, ParseIssue, TextBlock

from .common import MAX_BLOCKS, MAX_FILE_BYTES, MAX_TEXT_CHARS, check_file, make_document


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
    story_lengths = [_u32(word, pos + i * 4) for i in range(3, 11)]
    has_boxes = bool(story_lengths[6] or story_lengths[7])
    required_length = sum(story_lengths) if has_boxes else main_length
    if required_length > MAX_TEXT_CHARS:
        raise ConversionError("DOC: превышен лимит stories.")
    cb_mac = _u32(word, pos)
    if cb_mac > len(word):
        raise ConversionError("DOC: повреждён размер WordDocument.")
    word = word[:cb_mac]
    pos += long_count * 4
    pair_count = _u16(word, pos)
    pos += 2
    if pair_count <= 33:
        raise ConversionError("DOC: отсутствует таблица фрагментов.")
    pairs_pos = pos
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
        or cps[-1] < required_length
        or any(a >= b for a, b in zip(cps, cps[1:], strict=False))
    ):
        raise ConversionError("DOC: нарушен порядок символьных позиций.")
    chunks: list[bytes] = []
    for i in range(count):
        if cps[i] >= required_length:
            break
        length = min(cps[i + 1], required_length) - cps[i]
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
        encoded = b"".join(chunks)
        if has_boxes:
            if pair_count <= 59:
                raise ConversionError(
                    "DOC: отсутствуют таблицы рамок.", code="unsupported_doc_textbox"
                )
            start_cp = sum(story_lengths[:6])
            for story_index, pair_index in ((6, 56), (7, 58)):
                length = story_lengths[story_index]
                fc = _u32(word, pairs_pos + pair_index * 8)
                lcb = _u32(word, pairs_pos + pair_index * 8 + 4)
                if bool(length) != bool(lcb):
                    raise ConversionError(
                        "DOC: несогласованные границы рамок.", code="unsupported_doc_textbox"
                    )
                if length:
                    frame_text = encoded[start_cp * 2 : (start_cp + length) * 2].decode(
                        "utf-16-le", errors="strict"
                    )
                    _check_empty_textboxes(frame_text, _slice(table, fc, lcb))
                    # Field instructions/linked results are outside this proof.
                    if _u32(word, pairs_pos + (pair_index + 1) * 8 + 4):
                        raise ConversionError("DOC: поля в рамках.", code="unsupported_doc_textbox")
                start_cp += length
        return encoded[: main_length * 2].decode("utf-16-le", errors="strict")
    except UnicodeError as exc:
        raise ConversionError("DOC: некорректный Unicode в тексте.") from exc


def _check_empty_textboxes(text: str, plc: bytes) -> None:
    """Exhaustive story coverage: only paragraph marks, including ignored tail.

    PlcftxbxTxt has 22-byte FTXBXS entries. The final range is ignored by
    MS-DOC; its terminal CP may exceed ccpTxbx. Every actual range is bounded.
    With no content to order, shape-anchor reconstruction is unnecessary.
    """
    if not text or any(c != "\r" for c in text) or len(plc) < 30 or (len(plc) - 4) % 26:
        raise ConversionError(
            "DOC: непустые или недоказанные рамки требуют проверки.", code="unsupported_doc_textbox"
        )
    count = (len(plc) - 4) // 26
    cps = [_u32(plc, i * 4) for i in range(count + 1)]
    if (
        cps[0] != 0
        or any(a >= b for a, b in zip(cps, cps[1:], strict=False))
        or any(cp > len(text) for cp in cps[:-1])
    ):
        raise ConversionError("DOC: некорректные диапазоны рамок.", code="unsupported_doc_textbox")


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
    from .tables import binary_core_rows

    result.table_rows = binary_core_rows(path.name, raw)
    result.blocks = attach_cell_sources(result.blocks, raw)
    result.reader_facts = {
        "main_cp": len(raw.encode("utf-16-le")) // 2,
        "textbox_policy": "paragraph-marks-only; full-story-and-PLCF-check",
    }
    result.issues.append(
        ParseIssue(
            "doc_main_story",
            "DOC: прочитан основной текст; оформление и колонтитулы не импортируются.",
        )
    )
    return result


def attach_cell_sources(source_blocks: list[TextBlock], raw: str) -> list[TextBlock]:
    # CP-delimited physical cells prevent side columns (e.g. sample labels)
    # from becoming the tail of the preceding description.
    spans = []
    row_start = 0
    for marker in re.finditer(r"\x07\x07", raw):
        start_cp = row_start
        separators = [m.start() for m in re.finditer(r"\x07", raw[row_start : marker.end() - 1])]
        for index, offset in enumerate(separators):
            end_cp = row_start + offset
            spans.append((start_cp, end_cp, "LAST" if index == len(separators) - 1 else "OTHER"))
            start_cp = end_cp + 1
        row_start = marker.end()
    if len(spans) > MAX_BLOCKS:
        raise ConversionError("DOC: слишком много ячеек.")
    starts = [a for a, _, _ in spans]
    blocks = []
    for block in source_blocks:
        cp = int(block.source.position.split(":")[1].split("-")[0])
        index = bisect_right(starts, cp) - 1
        match_span = spans[index] if index >= 0 and cp < spans[index][1] else None
        if match_span:
            a, b, role = match_span
            block = replace(
                block, source=replace(block.source, cell_id=f"cp-cell:{a}-{b}", cell_role=role)
            )
        blocks.append(block)
    return blocks
