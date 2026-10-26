`synthetic.docx` создан функцией `coredescriptionw2e.smoke.create_docx`.
`synthetic.doc` получен из него LibreOffice 26.8.1.1, фильтр `MS Word 97`:

```sh
libreoffice --headless --convert-to 'doc:MS Word 97' --outdir tests/fixtures tests/fixtures/synthetic.docx
```

Оба файла содержат только вымышленные учебные фразы из `smoke.SYNTHETIC`.
Это реальные OOXML и OLE/MS-DOC, не переименованный TXT/RTF. В CI и portable
тестируется сам бинарный файл; LibreOffice при запуске приложения не нужен.
Текст fixtures и генератор распространяются по MIT проекта.
