"""DOCX paragraphs and nested table cells in XML order, without link resolution."""

import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from coredescriptionw2e.domain import ConversionError, ImportedDocument

from .common import MAX_FILE_BYTES, check_file, make_document


def read_docx(path: Path) -> ImportedDocument:
    check_file(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 10_000 or sum(i.file_size for i in infos) > MAX_FILE_BYTES:
                raise ConversionError("DOCX: превышен лимит распакованного контейнера.")
            # python-docx disables entities and network in its XML parser.
            if any(b"<!DOCTYPE" in archive.read(i) for i in infos if i.filename.endswith(".xml")):
                raise ConversionError("DOCX: DTD запрещён.")
        document = Document(str(path))
        body = document.element.body
        if body.xpath(".//w:txbxContent | .//w:altChunk | .//w:del | .//w:ins"):
            raise ConversionError(
                "DOCX: рамки, внешние блоки или исправления требуют ручной проверки."
            )
        entries: list[tuple[str, str]] = []
        for element in body.iter(qn("w:p")):
            chunks = []
            for item in element.iter():
                if item.tag == qn("w:t"):
                    chunks.append(item.text or "")
                elif item.tag in {qn("w:br"), qn("w:cr")}:
                    chunks.append("\n")
                elif item.tag == qn("w:tab"):
                    chunks.append("\t")
                elif item.tag == qn("w:noBreakHyphen"):
                    chunks.append("‑")
                elif item.tag == qn("w:softHyphen"):
                    chunks.append("\u00ad")
            raw = "".join(chunks)
            position = element.getroottree().getpath(element)
            # A manual Word line break remains a source boundary.
            entries.extend((f"{position}/line:{i}", line) for i, line in enumerate(raw.split("\n")))
        return make_document(path, entries, "\n".join(value for _, value in entries))
    except (zipfile.BadZipFile, KeyError, ValueError, etree.XMLSyntaxError) as exc:
        raise ConversionError("DOCX: повреждённый или неподдерживаемый документ.") from exc
