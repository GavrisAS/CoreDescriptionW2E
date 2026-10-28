"""Small auditable vocabulary; no geological inference from colours or references."""

import re

ROCKS = (
    (r"аргиллит\w*", "Аргиллит"),
    (r"алевролит\w*", "Алевролит"),
    (r"песчаник\w*", "Песчаник"),
    (r"гравелит\w*", "Гравелит"),
    (r"уголь|угля|углем|углём", "Уголь"),
    (r"известняк\w*", "Известняк"),
    (r"базальт\w*", "Базальт"),
    (r"серпентинит\w*", "Серпентинит"),
    (r"сидерит\w*", "Сидерит"),
    (r"глин[аы]", "Глина"),
    (r"доломит\w*", "Доломит"),
    (r"мергел\w*", "Мергель"),
    (r"конгломерат\w*", "Конгломерат"),
    (r"брекчи\w*", "Брекчия"),
    (r"туф\w*", "Туф"),
    (r"диабаз\w*", "Диабаз"),
)
MIXED = re.compile(r"\b(?:переслаиван\w*|чередован\w*|перемеж\w*|смешан\w*)\b", re.I)
PREFIX = re.compile(
    r"^(?:[=+@-]\S*;?\s*)?(?:(?:Верх|Низ)\s*:\s*|\d+(?:[.,]\d+)?\s*м\s*[-–—:]?\s*)?",
    re.I,
)
OIL = re.compile(r"\b(?:нефт\w*|углеводород\w*)\b", re.I)
NEGATIVE = re.compile(
    r"\b(?:нет|отсутств\w*|без|не|ненефтенасыщ\w*|ненасыщ\w*)\b",
    re.I,
)
EVIDENCE = re.compile(
    r"запах\w*\s+нефти|(?:выпот\w*|след\w*|пятн\w*|пропитк\w*)\s+нефт\w*|"
    r"нефтенасыщ\w*|насыщ\w*\s+(?:нефтью|углеводород\w*)|"
    r"(?:признак\w*|проявлен\w*)\s+углеводород\w*",
    re.I,
)


UNCERTAIN = re.compile(
    r"\b(?:возмож\w*|предполож\w*|сомнитель\w*|вероятн\w*|неподтвержд\w*|неяс\w*)\b", re.I
)


def lithology(text: str) -> str | None:
    opening = re.split(r"(?<!\d)[.;!?](?!\d)", text, maxsplit=1)[0].strip()
    opening = PREFIX.sub("", opening).strip()
    matches = sorted(
        (m.start(), label)
        for pattern, label in ROCKS
        for m in re.finditer(rf"\b(?:{pattern})\b", opening, re.I)
    )
    labels = list(dict.fromkeys(label for _, label in matches))
    all_labels = {label for pattern, label in ROCKS if re.search(rf"\b(?:{pattern})\b", text, re.I)}
    if MIXED.search(opening) and len(labels) > 1 and all_labels == set(labels):
        return " / ".join(labels)
    if len(labels) == 1 and matches[0][0] == 0 and len(all_labels) == 1:
        return labels[0]
    return None


def hydrocarbons(text: str) -> str | None:
    # Preserve source wording (including weak evidence); negatives never become V.
    clauses = re.split(r"(?<=[.!?;])\s+", text)
    return (
        " ".join(
            clause
            for clause in clauses
            if EVIDENCE.search(clause)
            and not NEGATIVE.search(clause)
            and not UNCERTAIN.search(clause)
        )
        or None
    )


def ambiguous_hydrocarbons(text: str) -> bool:
    return any(
        OIL.search(clause)
        and not NEGATIVE.search(clause)
        and (UNCERTAIN.search(clause) or not EVIDENCE.search(clause))
        for clause in re.split(r"(?<=[.!?;])\s+", text)
    )
