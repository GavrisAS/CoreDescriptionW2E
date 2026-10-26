# Сторонние компоненты

Код приложения — MIT. Самостоятельный MS-DOC reader использует **olefile 0.47
(BSD)** для OLE. Antiword, LibreOffice, Office и GPL-декодеры не входят в portable.
LibreOffice используется разработчиком только для создания нейтрального fixture.

Точные Python-версии и хеши — в `uv.lock`:

| Компонент | Условия |
|---|---|
| CPython | PSF и вложенные уведомления поставки |
| python-docx, openpyxl, et-xmlfile | MIT |
| olefile | BSD, полный текст из wheel/source |
| lxml | BSD; отдельные лицензии встроенных libxml2/libxslt в LICENSES wheel |
| defusedxml, typing-extensions | PSF |
| PySide6-Essentials, Shiboken6 | LGPL-3.0, community-поставка |
| Qt из qtbase/qtsvg | LGPL-3.0, отдельные встроенные компоненты/уведомления |
| PyInstaller bootloader | GPL с исключением для распространения приложения |

`scripts/package_licenses.py` выполняется только при сборке. Полные license,
notice, copyright и attribution-файлы извлекаются из точных source archives и
wheel, включая CPython и bootloader. Qt/PySide 6.12.0: официальные qtbase, qtsvg,
pyside-setup и закреплённые SHA-256 с download.qt.io. Остальные sdists сверяются
с lockfile. Неучтённая Qt DLL, несовпадение хеша или отсутствие лицензии блокируют сборку.

ZIP содержит `third-party-licenses/`, `third-party-sources/` и
`third-party-inventory.json`. Точные исходники Qt/PySide/Shiboken со своими
build scripts и встроенными компонентами предоставляются **в самом ZIP**, не
только ссылкой. Сторонние компоненты не изменяются. Инструкции пересборки — в
архивах и [Qt for Python build documentation](https://doc.qt.io/qtforpython-6/building_from_source/index.html).

Qt остаётся отдельными заменяемыми DLL внутри `_internal/PySide6`. Разрешены
замена совместимыми библиотеками, обратная разработка для отладки модификаций,
пересборка приложения из открытых исходников и запуск изменённых версий.
Никаких дополнительных ограничений не вводится. Используется PyInstaller onedir.

Основания: [Qt licensing](https://doc.qt.io/qt-6/licensing.html),
[обязательства LGPL](https://www.qt.io/development/open-source-lgpl-obligations),
[Qt for Python](https://doc.qt.io/qtforpython-6/).
Смена lockfile/состава DLL требует повторной проверки исходников и лицензий.
