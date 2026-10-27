"""DOCX paragraphs and nested table cells in XML order, without link resolution."""

import zipfile
from dataclasses import replace
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from coredescriptionw2e.domain import ConversionError, ImportedDocument, SourceRef, SourceRow

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
                "DOCX: рамки, внешние блоки или исправления требуют ручной проверки.",
                code="unsupported_docx_content",
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
        result = make_document(path, entries, "\n".join(value for _, value in entries))
        from .tables import cell

        cell_roles = {}
        for n, row in enumerate(body.iter(qn("w:tr"))):
            position = row.getroottree().getpath(row)
            cells = []
            physical_cells = row.findall(qn("w:tc"))
            for column, tc in enumerate(physical_cells):
                # Entries already preserve tabs/manual line breaks. Read physical
                # cells, not python-docx's duplicated proxies for merged cells.
                prefix = tc.getroottree().getpath(tc) + "/"
                cell_roles[prefix] = "LAST" if column == len(physical_cells) - 1 else "OTHER"
                raw = "\n".join(value for pos, value in entries if pos.startswith(prefix))
                cells.append(cell(path.name, f"{position}/cell:{column}", n, raw))
            result.table_rows.append(SourceRow(SourceRef(path.name, position, n), cells))
        result.blocks = [
            replace(
                block,
                source=replace(block.source, cell_id=prefix[:-1], cell_role=cell_roles[prefix]),
            )
            if (
                prefix := next(
                    (p for p in reversed(cell_roles) if block.source.position.startswith(p)), None
                )
            )
            else block
            for block in result.blocks
        ]
        return result
    except (zipfile.BadZipFile, KeyError, ValueError, etree.XMLSyntaxError) as exc:
        raise ConversionError(
            "DOCX: повреждённый или неподдерживаемый документ.", code="invalid_docx"
        ) from exc
