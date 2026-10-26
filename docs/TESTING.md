# Проверки

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

CI: Linux/Windows, Python 3.14. Fixtures проверяют два долбления, пропуск и
рестарт номера, кириллицу, таблицы/переносы, разные тире, двойные скобки,
сдвиг ГИС, относительные интервалы, отрицание УВ, неизвестные поля и инъекции.
Бинарный DOC создан независимым LibreOffice и читается portable-адаптером.
Office/LibreOffice/Antiword в CI не устанавливаются и при чтении не вызываются.
GUI smoke имеет timeout; frozen acceptance конвертирует DOC и DOCX в настоящие
XLSX с проверками текстов, строк, формул, номеров и оформления.

Приватная локальная проверка с нейтральными именами (подставьте свои):

```sh
uv run coredescriptionw2e --input-dir data --template data/template.xlsx --json > data/conversion-status.json
uv run python scripts/local_validation.py --word data/input.doc --xlsx data/output/input.xlsx
```

`validation-summary.json` рядом с XLSX содержит количества, интервалы, номера,
число символов/блоков W и результаты проверок без полных описаний. Отчёт всё
равно приватен, не коммитить. `--expected data/expected.json` сверяет локальные
ожидания, например `{"runs":2,"layers":3,"distribution":[2,1]}` для synthetic.
`--reference-docx data/reference.docx` сравнивает текст с независимым локальным
импортом DOC, игнорируя только пробельную раскладку. PASS локального аудита
относится к его проверкам, не ко всему продукту и не к отсутствию предупреждений.

Формулы проверяются по тексту/адресам, кеш не вычисляется Python. Для ручной
приёмки откройте локальный результат в Excel, проверьте пересчёт L/M, W и
предупреждения. Машинная проверка не является визуальной приёмкой в Excel.


Дополнительные regression gates: целые метки «Проход»/«проходка» только в шапке
долбления, allocation patch и конфликты, ZIP/manifest/source/version mismatch,
ошибка публикации; handoff fresh extract, dirty snapshot/diff, privacy, fixture
spoofing, traversal/symlink/special files/duplicates, tamper и XZ truncation.
Число тестов берётся из текущего запуска, не из исторической документации.
После каждого содержательного Codex-запроса обязательны [create+verify](HANDOFF.md).
