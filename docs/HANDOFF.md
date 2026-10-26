# Приватная передача после каждого запроса Codex

После **каждого содержательного запроса Codex** последний обязательный шаг —
создать и проверить новый локальный handoff. Это относится также к частичным
результатам и ошибкам: записать FAIL/BLOCKED/NOT_RUN/PENDING и попытаться создать
пакет. Никакой исторический SHA или число тестов не считается текущим PASS.
Правило исполняет агент по AGENTS.md; скрипт не обнаруживает окончание произвольного
промпта автоматически. GitHub-only операции без локальных изменений допускают
запись статуса и пакет по запросу.

```sh
uv run python -m scripts.attest --remote
uv run python scripts/handoff.py create
uv run python scripts/handoff.py verify --archive artifacts/handoff/<имя>.tar.xz
```

`attest` запускает locked sync, Ruff, ty, pytest; записывает exact argv, exit code,
ОС и версии инструментов без stdout/tracebacks. `--remote` получает факты через
`gh run view --json` для текущего HEAD и API Releases. Нет доступа — NOT_RUN;
незавершённый run — PENDING. REMOTE DIRECT EVIDENCE содержит ID, URL, SHA и jobs.
Publication metadata и downloadable assets проверяет publishing job; для отдельного
аудита скачайте три assets и сопоставьте SHA. При ошибке helper всё равно попытайтесь
выполнить create: без актуального evidence gates будут NOT_RUN, а не вымышленный PASS.
Не передавайте helper секретные аргументы или необезличенные диагностические файлы.

`artifacts/handoff-evidence.json` — локальный typed receipt, связанный с fingerprint.
`create` использует его только при совпадении fingerprint; устаревший evidence
игнорируется с кодом `stale_evidence_ignored`. Для явного файла есть `--evidence`:
несовпадение fingerprint отклоняется. Не подставляйте ожидаемые результаты.
Диагностика допускает только обезличенные коды, количества и ограничения;
не тексты, интервалы, имена входов или реальные локальные пути.

Результат — **один** `coredescriptionw2e-handoff-<UTC>-<short-sha>-<random-id>.tar.xz`
и соседний `.tar.xz.sha256` в ignored `artifacts/handoff/`. В финальном ответе
обязательны абсолютные локальные пути обоих файлов, ID, размер и create/verify status.
Файлы предназначены для приватного прикрепления в ChatGPT, не GitHub artifacts,
Releases, issues или Git. Не загружайте их автоматически.

Внутри: `manifest.json`, полный `source/` snapshot разрешённых tracked файлов
и разрешённых untracked исходников; `review/REPORT.md` (A–J), `SOURCE_STATE.json`,
`GATES.json`, `COMMANDS.json`, `TESTS.json`, `RELEASE.json`, `DIAGNOSTICS.json`,
`DIFF.patch`, `FILE_MANIFEST.json`. Поля JSON имеют schema_version=1 на уровне
manifest/source/evidence. HEAD, branch, origin/main, dirty/changes/deleted и список
SHA/size связывают пакет с фактическим состоянием. Source fingerprint — SHA-256
канонического JSON (sorted keys, indent=2, UTF-8, LF) отсортированного списка
`{path,size,sha256}` всех файлов `source/`. Dirty diff включает staged+unstaged
изменения относительно HEAD; untracked передаются точными файлами snapshot.
Удалённые tracked файлы отражаются в SOURCE_STATE и patch.
`FILE_MANIFEST` перечисляет payload до себя, manifest перечисляет payload включая
FILE_MANIFEST; самоссылок и вложенного source.zip нет.

До публикации: allowlist source, denylist private/generated путей, проверка
секретов/локальных путей, лимиты 4 МиБ/файл и 32 МиБ/payload, запрет symlinks,
special files, traversal, дубликатов без учёта регистра и опасных TAR-метаданных.
Разрешены только две синтетические Word fixtures с зафиксированными публичными
SHA. Их замена требует отдельного аудита и обновления allowlist. Пустой tracked
`data/.gitkeep` исключается. Qt source bundles, документы/книги/результаты, кеши,
.git, build/dist и предыдущие архивы не включаются. Сканер — дополнительный барьер:
передавайте только проверенные исходники и обезличенные receipts, он не способен
семантически распознать любой секрет или скопированную геологию в произвольном тексте.

Создание: временный архив → внешняя/внутренняя проверка → свежая безопасная
распаковка → atomic rename. Сначала публикуется проверенный sidecar, затем архив;
прерывание между ними оставит только sidecar, не непроверенный готовый архив.
Verify проверяет завершённость XZ, канонический TAR, имена, typed receipts,
все size/SHA, fingerprint и fresh extract. Любой FAIL возвращает nonzero без
сырого stdout/traceback. Если privacy gate отклоняет источник, не обходите его:
исправьте/редактируйте чувствительные данные локально, укажите фактический FAIL.

Перед следующим аудитом проверьте текущие gates в generated review. Ручная
визуальная приёмка сформированного Excel остаётся отдельным действием пользователя.
