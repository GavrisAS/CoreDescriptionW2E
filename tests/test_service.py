import shutil

from coredescriptionw2e.cli import main
from coredescriptionw2e.service import convert_batch
from coredescriptionw2e.smoke import verify_output


def test_batch_isolation_and_nonrecursive(sample):
    folder, word, template = sample
    (folder / "Поврежден.doc").write_bytes(b"not OLE")
    (folder / "~$temporary.docx").write_bytes(b"temp")
    sub = folder / "subfolder"
    sub.mkdir()
    shutil.copyfile(word, sub / "Вложенный.docx")
    progress = []
    results = convert_batch(
        folder, template, progress=lambda n, total, result: progress.append((n, total))
    )
    assert sorted(r.status for r in results) == ["error", "skipped", "success"]
    assert progress == [(1, 3), (2, 3), (3, 3)]
    verify_output(folder / "output" / "Учебный.xlsx")


def test_collision_blocks_both(sample):
    folder, word, template = sample
    (folder / (word.stem + ".doc")).write_bytes(b"invalid")
    results = convert_batch(folder, template)
    assert len(results) == 2 and all(r.status == "error" for r in results)
    assert not list((folder / "output").rglob("*.xlsx"))
    assert len(list((folder / "output").glob("batch-*.json"))) == 1


def test_existing_output_requires_explicit_overwrite(sample):
    folder, _, template = sample
    first = convert_batch(folder, template)[0]
    assert first.output is not None
    original = first.output.read_bytes()
    assert convert_batch(folder, template)[0].status == "skipped"
    assert first.output.read_bytes() == original
    assert convert_batch(folder, template, overwrite=True)[0].status == "success"


def test_cli_exit_codes_and_json(sample, capsys):
    folder, _, template = sample
    args = ["--input-dir", str(folder), "--template", str(template), "--json"]
    assert main(args) == 0
    assert '"layers": 3' in capsys.readouterr().out
    assert main(args) == 1
    assert main(["--input-dir", str(folder / "missing"), "--template", str(template)]) == 2


def test_existing_case_variant_is_not_overwritten(sample):
    folder, _, template = sample
    output = folder / "output"
    output.mkdir()
    existing = output / "учебный.xlsx"
    existing.write_bytes(b"existing")
    assert convert_batch(folder, template, overwrite=True)[0].status == "error"
    assert existing.read_bytes() == b"existing"
