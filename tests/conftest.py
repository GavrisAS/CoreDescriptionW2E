from pathlib import Path

import pytest

from coredescriptionw2e.smoke import create_docx, create_template


@pytest.fixture
def sample(tmp_path: Path) -> tuple[Path, Path, Path]:
    folder = tmp_path / "Word с пробелами"
    folder.mkdir()
    word = folder / "Учебный.docx"
    template = tmp_path / "Шаблон.xlsx"
    create_docx(word)
    create_template(template)
    return folder, word, template
