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

`<имя>.validation.json` рядом с XLSX содержит количества, интервалы, номера,
число символов/блоков W и результаты проверок без полных описаний. Отчёт всё
равно приватен, не коммитить. `--expected data/expected.json` сверяет локальные
ожидания, например `{"runs":2,"layers":3,"distribution":[2,1]}` для synthetic.
`--reference-docx data/reference.docx` сравнивает текст с независимым локальным
импортом DOC, игнорируя только пробельную раскладку. PASS локального аудита
относится к его проверкам, не ко всему продукту и не к отсутствию предупреждений.

Формулы проверяются по тексту/адресам, кеш не вычисляется Python.
Для проверки J/K/L скрипт выполняет `recovery_all_run_rows`: оба числа, точная формула
с адресами своей строки по semantic mapping и процентный формат каждого слоя,
включая границы долблений и отсутствующие метрики. Synthetic regression содержит
долбления из трёх и двух слоёв с разными показателями и переставленные колонки.
Проверяются строгий заголовок «Интервал N», парная метрика в шапке, конфликты,
доказанное пустое долбление и отказ для неоднозначных таблиц «Керн N».
Для ручной приёмки откройте локальный результат в Excel, проверьте пересчёт L/M, W и
предупреждения. Машинная проверка не является визуальной приёмкой в Excel.


Дополнительные regression gates: целые метки «Проход»/«проходка» только в шапке
долбления, allocation patch и конфликты, ZIP/manifest/source/version mismatch,
ошибка публикации; handoff fresh extract, dirty snapshot/diff, privacy, fixture
spoofing, traversal/symlink/special files/duplicates, tamper и XZ truncation.
Число тестов берётся из текущего запуска, не из исторической документации.
После каждого содержательного Codex-запроса обязательны [create+verify](HANDOFF.md).


Новые синтетические проверки: CORE_INTERVAL с физическими source rows, частями и картой покрытия,
повтором ID/интервала, сохранением всех исходных записей и исключением только доказанных дублей, двумя типами no-core,
процентами и сводками; точный настраиваемый порог процентов; отсутствие
фиктивных слоёв/ГИС; whitelist review и отрицательные случаи structural failure,
включая ошибочную warning severity; отделение боковой колонки от W и сохранение
продолжения в следующей правой ячейке. DOC рамки генерируются в памяти как
FIB/CLX/PlcftxbxTxt: пустой story разрешён, любой содержательный символ,
неверные диапазоны и усечение блокируются. Реальные документы не нужны.

Frozen smoke по-прежнему проверяет две исходные synthetic DOC/DOCX книги и
дополнительно две review-книги: core intervals и послойное перекрытие.
Receipt включает core_interval_gate/review_policy_gate, число core records и
review_documents; `release.verify_bundle` требует эти поля для новых сборок.
Windows quality и verify распакованного EXE не устанавливают Office/LibreOffice.

`scripts/local_validation.py` выбирает основной лист по схеме, даже если активен
Контроль, поддерживает оба режима и выдаёт REVIEW_REQUIRED отдельно от PASS.
Это workbook roundtrip против модели, а не независимый oracle исходного Word.
Для приватной приёмки требуется дополнительное сравнение W/метрик по прямым
OOXML-ячейкам; DOC можно сверить с уже доступным локальным dev-конвертером.
Описание может продолжаться в следующей правой ячейке до нового заголовка.
Число заголовков само по себе не доказывает полноту W. Исходные SHA и шаблон
проверяются до/после. Упоминаемые пути — примеры; реальные имена/данные/отчёты
остаются в ignored каталоге acceptance-<timestamp> внутри data, никогда в CI или handoff.

## Корректирующая партия 1

Изменённые старые ожидания объясняются ответами специалистов в
[BUSINESS_RULES.md](BUSINESS_RULES.md): приоритет коробок отменён, Верх/Низ
могут стать частями, отрицание УВ не положительная V, J<K — review, output24,
locks/target_exists — явные skipped. Тесты не отключались.
`test_business_rules.py` проверяет все ветки разбиения/полноты/дедупликации,
нули, комбинации U, literal V, X, русские статусы/легенду и двусторонние ссылки.
Синтетический census из семи кандидатов включает разные расширения, кириллицу,
точки, коллизии, неподдерживаемую структуру и lock; ни один исход не теряется.

```sh
uv run python -m scripts.business_acceptance --input-dir data --template data/template.xlsx --output-dir "$ACCEPTANCE_DIR" --independent-dir "$REFERENCE_DIR"
```

Скрипт требует новый output. Независимый OOXML проход не использует application
reader/parser: отдельно извлекает описания физических ячеек/слоёв. Для DOC нужны
локальные независимые DOCX-копии (например, LibreOffice как dev oracle).
Без них independent coverage NOT_RUN, а не PASS. Проверяются все родители,
исходные блоки, диапазоны частей, числовой баланс с журналом дублей, W, J/K/L,
невыдуманные глубины, X, ссылки, SHA входов/шаблона до/после. REPORT.md/report.json
и полный batch JSON приватны; их нельзя включать в handoff/CI/Release.

Frozen smoke дополнительно исполняет `business_smoke` в распакованном EXE:
Верх/Низ, X/core24, полный дубль, нулевой K без слоя, суммы родителей и формулы.
Receipt `business_rules_gate=PASS`, `output_schema=core24-v1` обязателен для
новых production releases. CI Linux/Windows и неизменный main release workflow
проверяют source SHA, ZIP/EXE hash и frozen smoke до публикации.

Физический firewall smoke и пересчёт именно в Microsoft Excel требуют отдельного
свидетельства. Socket-block test/LibreOffice не выдаются за эти проверки.
Визуальная/геологическая приёмка пользователем остаётся PENDING.
