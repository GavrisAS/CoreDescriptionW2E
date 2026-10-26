# Windows portable

Собирать PE только на Windows x64, Python 3.14, PowerShell 7:
`./scripts/build_windows.ps1 -Commit (git rev-parse HEAD)`.
При локальной сборке без RUNNER_TEMP задайте его временным каталогом.
Сеть нужна только сборке для uv и проверенных исходников Qt.

Workflow запускает полный Linux/Windows CI, Windows тесты и PyInstaller onedir
windowed. Только Qt DLL из разрешённых qtbase/qtsvg допускаются в ZIP. Все
лицензии и точные сторонние исходники включаются в пакет. Это увеличивает ZIP,
но обеспечивает непосредственный доступ к LGPL source.

После упаковки ZIP распаковывается в чистый каталог с пробелами/кириллицей.
Рабочая папка вне checkout, PATH содержит только системные каталоги. EXE
выполняет GUI smoke и настоящий DOC+DOCX → XLSX smoke с проверкой формул,
текстов и оформления. Timeout каждого запуска 90 секунд. Неуспех блокирует upload.

Artifact: ZIP, SHA-256, manifest с версией/commit/backend. ZIP содержит приложение,
DLL, синтетический DOC для smoke, лицензии и исходники. Пользовательских данных нет.
Release только по `vX.Y.Z` либо workflow_dispatch с `publish=true`, после всех
проверок. Всегда prerelease до пользовательской приёмки. Тег соответствует
manifest; существующий Release не заменяется, теги не перемещаются.
Обычный push создаёт только CI artifact. Для проверки dispatch: `publish=false`.
