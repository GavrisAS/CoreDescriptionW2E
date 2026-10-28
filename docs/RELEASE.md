# Автоматический Windows portable Release

[Скачать Windows portable](https://github.com/GavrisAS/CoreDescriptionW2E/releases).
Каждый push в `main` автоматически запускает production workflow. PR и ручной
`diagnostic` проверяют сборку без публичного тега/Release. Созданные теги workflow
не запускают. Все production runs занимают общий concurrency slot с
`cancel-in-progress: false`, `queue: max`: ожидающие runs сохраняются, до 100.
[Синтаксис GitHub](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).
Переполнение очереди или failure не считается успешной производственной сборкой.

После quality Linux+Windows свежие tags и полный API releases определяют следующий
`v0.1.P`: максимум занятого patch + 1, либо `v0.1.0`, если линия пуста. Tag без Release
тоже занимает patch; Release без tag блокирует allocation. Malformed tags и другие
линии версий не участвуют. Run number не используется. Ошибка до создания тега
не расходует patch. Ошибка после push тега оставляет занятый tag/draft для аудита;
следующая новая production операция возьмёт следующий patch. Нельзя двигать теги,
перезаписывать assets или повторно использовать занятый номер.

Версия фиксируется временным `_build_version.py` **до PyInstaller**, включается в
EXE, заголовок GUI, CLI `--version` и frozen smoke receipt. В runtime environment
не участвует. Исходники/pyproject остаются базовой dev-версией `0.1.0`; локальная
Windows сборка по умолчанию маркируется `0.1.0+local` и не бронирует публичный номер:

```powershell
./scripts/build_windows.ps1 -Commit (git rev-parse HEAD)
```

Сборка требует Windows x64, Python 3.14, uv и PowerShell 7. Сеть нужна только
разработчику/CI для зависимостей и точных проверенных LGPL sources; обработка
пользовательских документов полностью офлайн. Reader Word 97–2003 не требует
Office/LibreOffice и внешних декодеров.

Build выполняет quality checks, PyInstaller onedir/windowed, аудит Qt DLL,
лицензий и точных sources. Из **этого ZIP** выполняются GUI, CLI help/version и
synthetic DOC+DOCX → XLSX и CORE_INTERVAL/REVIEW_REQUIRED с проверкой формул, W и оформления. Чистый
каталог с пробелами/кириллицей находится вне checkout, PATH — только системный,
timeout каждого EXE 90 секунд. Внешний manifest содержит версию, source commit,
ZIP/EXE SHA-256, frozen receipt. В ZIP есть внутренний manifest с версией/source.
Publishing job снова проверяет downloaded artifact, внутренний manifest, hash
и распакованный frozen EXE. Только этому job дано `contents: write`.

Перед тегом повторно проверяются tags/releases и exact checkout SHA. Создаётся
annotated tag на проверенный `github.sha`, затем draft prerelease с ровно тремя
assets: `CoreDescriptionW2E-v0.1.P-windows-x64.zip`, его `.sha256`, `manifest.json`.
API digest и повторное скачивание сверяются до и после публикации. Ошибка — FAIL,
без замены assets; draft и tag сохраняются для расследования. Публикация, а не
только успешный build, определяет release gate. Actions artifact остаётся
диагностическим каналом. Приватные документы и handoff туда никогда не попадают.

Повторить тот же SHA разрешено только **новым явным production dispatch**:

```sh
gh workflow run windows-build.yml --ref main -f mode=production
```

Он получает новый patch. Re-run существующего production run (`run_attempt > 1`)
отклоняется, чтобы не создавать duplicate Release. Для диагностики:

```sh
gh workflow run windows-build.yml --ref main -f mode=diagnostic
```

Пока нет ручной визуальной приёмки Excel, версии публикуются как **prerelease**.
Поэтому README ведёт на `/releases`, не `/releases/latest`. Пользовательская
приёмка PENDING и ограничения форматов явно указаны в release notes. После
отдельного подтверждения пользователя можно вручную повысить конкретный выпуск
до stable без изменения файлов/тега. Машинные tests не заменяют эту приёмку.

Скачайте ZIP и sidecar из одного Release. В PowerShell:

```powershell
Get-FileHash ./CoreDescriptionW2E-v0.1.P-windows-x64.zip -Algorithm SHA256
Get-Content ./CoreDescriptionW2E-v0.1.P-windows-x64.zip.sha256
```

Сопоставьте hex, распакуйте **весь** ZIP в новую папку, запустите EXE. Python и
Office пользователю устанавливать не нужно. Формулы L/M пересчитывает Excel.
Ограничения DOC/DOCX и диагностика неизвестных значений перечислены в README.


При инфраструктурной ошибке после upload сохраняются draft и занятый tag.
После исправления допускается завершить публикацию **того же** draft: сначала
сверить source SHA через API annotated tag, подтверждённые quality/frozen jobs,
три assets, SHA и manifest; затем открыть draft и проверить публичные downloads.
Никаких повторных uploads/замены файлов/retag. Это восстановление незавершённой
публикации, а не re-run сборки. Draft читается API по ID из списка Releases:
endpoint `/releases/tags/...` до публикации возвращает 404.


Новый frozen receipt содержит `core_interval_gate=PASS`, `review_policy_gate=PASS`,
`core_interval_records=3`, `review_documents=2` наряду с прежними baseline gates.
`verify_bundle` проверяет их до публикации. Дополнительно обязательны business_rules_gate=PASS и output_schema=core24-v1:
проверяются доказанные части, дедупликация, нулевой выход и X в том же EXE.
CORE_INTERVAL переносит полные исходные описания в доказанные части либо целиком;
неизвестные глубины геологических слоёв не заявляются. Release Notes
явно сохраняют REVIEW_REQUIRED и PENDING приёмку, независимо от успешной сборки.
