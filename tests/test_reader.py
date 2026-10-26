import struct
import zipfile
from pathlib import Path

import pytest
from docx import Document

from coredescriptionw2e.domain import ConversionError
from coredescriptionw2e.smoke import SYNTHETIC
from coredescriptionw2e.word.common import read_word
from coredescriptionw2e.word.doc_reader import extract_main_story


def test_docx_table_order_and_raw(sample):
    _, word, _ = sample
    document = read_word(word)
    assert [b.text for b in document.blocks] == list(SYNTHETIC)
    assert "Запах\nнефти" in document.raw_text
    assert "tc" in document.blocks[7].source.position
    assert [b.source.order for b in document.blocks] == list(range(len(SYNTHETIC)))


def test_binary_doc_independent_fixture():
    document = read_word(Path("tests/fixtures/synthetic.doc"))
    assert [b.text for b in document.blocks] == list(SYNTHETIC)
    assert document.blocks[7].source.position.startswith("cp:")


def test_docx_manual_line_break(sample):
    _, word, _ = sample
    doc = Document()
    doc.add_paragraph("до\nпосле")
    doc.save(word)
    assert [b.text for b in read_word(word).blocks] == ["до", "после"]


def test_docx_rejects_zip_bomb(sample):
    _, word, _ = sample
    with zipfile.ZipFile(word, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b" " * (64 * 1024 * 1024 + 1))
    with pytest.raises(ConversionError, match="лимит"):
        read_word(word)


def test_docx_rejects_dtd(sample):
    _, word, _ = sample
    with zipfile.ZipFile(word, "w") as archive:
        archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY x SYSTEM "file:///no">]><x/>')
    with pytest.raises(ConversionError, match="DTD"):
        read_word(word)


def fib_and_piece(text: str, compressed: bool = False) -> tuple[bytes, bytes]:
    word = bytearray(2048)
    struct.pack_into("<HH", word, 0, 0xA5EC, 0xC1)
    struct.pack_into("<H", word, 32, 14)
    struct.pack_into("<H", word, 62, 22)
    struct.pack_into("<I", word, 64, len(word))
    struct.pack_into("<I", word, 76, len(text))
    struct.pack_into("<H", word, 152, 93)
    struct.pack_into("<II", word, 154 + 33 * 8, 0, 21)
    data = text.encode("cp1252" if compressed else "utf-16-le")
    word[1536 : 1536 + len(data)] = data
    fc = (1536 * 2) | 0x40000000 if compressed else 1536
    plc = struct.pack("<IIHIH", 0, len(text), 0, fc, 0)
    return bytes(word), b"\x02" + struct.pack("<I", len(plc)) + plc


@pytest.mark.parametrize("compressed,text", [(False, "Кириллица\r"), (True, "Latin ‚ —\r")])
def test_piece_table_encodings(compressed, text):
    word, table = fib_and_piece(text, compressed)
    assert extract_main_story(word, table) == text


@pytest.mark.parametrize("kind", ["encrypted", "bounds", "cp", "old", "textbox"])
def test_doc_rejects_bad_structures(kind):
    word, table = fib_and_piece("Тест\r")
    w, t = bytearray(word), bytearray(table)
    if kind == "encrypted":
        struct.pack_into("<H", w, 10, 0x100)
    elif kind == "bounds":
        struct.pack_into("<I", t, 15, 999999)
    elif kind == "cp":
        struct.pack_into("<I", t, 5, 4)
    elif kind == "old":
        struct.pack_into("<H", w, 2, 0x65)
    else:
        struct.pack_into("<I", w, 64 + 9 * 4, 10)
    with pytest.raises(ConversionError):
        extract_main_story(bytes(w), bytes(t))
