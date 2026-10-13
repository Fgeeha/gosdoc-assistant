"""Извлечение ссылок на НПА из текста и разбор заголовка акта при загрузке.

Ключ акта: вид + номер (+ дата для подзаконных актов — их номера не уникальны).
"""

import re
from dataclasses import dataclass
from datetime import date

KINDS = {
    "federal_law": "Федеральный закон",
    "federal_constitutional_law": "Федеральный конституционный закон",
    "code": "Кодекс",
    "presidential_decree": "Указ Президента РФ",
    "government_decree": "Постановление Правительства РФ",
    "government_order": "Распоряжение Правительства РФ",
    "order": "Приказ",
    "gost": "ГОСТ",
}
MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "января",
            "февраля",
            "марта",
            "апреля",
            "мая",
            "июня",
            "июля",
            "августа",
            "сентября",
            "октября",
            "ноября",
            "декабря",
        ],
        start=1,
    )
}

_DATE = (
    r"(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}\s+(?:"
    + "|".join(MONTHS)
    + r")\s+\d{4})(?:\s*(?:г\.|года?))?"
)
_NUM = r"(?:№|N)\s*(?P<number>[\w\-./]+?(?:-ФКЗ|-ФЗ)?)(?=[\s,;:)»\"]|\.(?:\s|$)|$)"
_KIND_WORDS = [
    ("federal_constitutional_law", r"Федеральн\w*\s+конституционн\w*\s+закон\w*"),
    ("federal_law", r"Федеральн\w*\s+закон\w*"),
    ("presidential_decree", r"Указ\w*\s+Президента\s+(?:Российской\s+Федерации|РФ)"),
    ("government_decree", r"[Пп]остановлени\w*\s+Правительства\s+(?:Российской\s+Федерации|РФ)"),
    ("government_order", r"[Рр]аспоряжени\w*\s+Правительства\s+(?:Российской\s+Федерации|РФ)"),
    ("order", r"[Пп]риказ\w*(?:\s+[А-ЯЁ][\w«»\"-]*){0,6}"),
]
_ACT = re.compile(
    "(?P<kind>"
    + "|".join(f"(?P<{k}>{p})" for k, p in _KIND_WORDS)
    + r")"
    + r"(?:\s+от\s+(?P<date>"
    + _DATE
    + r"))?\s*"
    + _NUM
    + r"(?:\s+от\s+(?P<date2>"
    + _DATE
    + r"))?"
)
_BARE_FZ = re.compile(r"(?<![\w-])(?:№|N)?\s*(\d{1,4}-ФК?З)(?![\w-])")
_GOST = re.compile(r"ГОСТ(?:\s+Р)?(?:\s+ИСО(?:/МЭК)?)?\s+[\d.]+-\d{2,4}")
_EDITION = re.compile(r"\(?\s*в\s+ред(?:\.|акции)\s+от\s+(" + _DATE + r")\s*\)?", re.IGNORECASE)
_ARTICLE = re.compile(
    r"(?:(?:ч\.|част\w*)\s*(?P<part>\d+(?:\.\d+)?)\s+)?(?:ст\.|стать\w*)\s*(?P<article>\d+(?:\.\d+)*)"
    r"(?:\s+(?:ч\.|част\w*)\s*(?P<part2>\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)


def parse_date(text: str) -> date | None:
    text = re.sub(r"\s*(?:г\.|года?)$", "", text.strip())
    if m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text):
        d, mo, y = map(int, m.groups())
    elif m := re.fullmatch(r"(\d{1,2})\s+(\w+)\s+(\d{4})", text):
        d, mo, y = int(m.group(1)), MONTHS.get(m.group(2), 0), int(m.group(3))
    else:
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def canonical_key(kind: str, number: str, adopted_at: date | None) -> str:
    number = re.sub(r"\s+", "", number).upper().replace("N", "№").lstrip("№")
    if kind in ("federal_law", "federal_constitutional_law", "gost"):
        return f"{kind}:{number}"
    return f"{kind}:{number}:{adopted_at.isoformat() if adopted_at else '?'}"


@dataclass(frozen=True)
class NpaRef:
    text: str  # как записано в документе
    kind: str
    number: str
    adopted_at: date | None
    edition_date: date | None
    article: str | None
    part: str | None
    paragraph: int  # номер абзаца документа

    @property
    def key(self) -> str:
        return canonical_key(self.kind, self.number, self.adopted_at)

    @property
    def path(self) -> str | None:
        if not self.article:
            return None
        return f"ст. {self.article}" + (f" ч. {self.part}" if self.part else "")


def _article_before(text: str, start: int) -> tuple[str | None, str | None]:
    """«частью 1 статьи 12 Федерального закона …» — статья перед названием акта."""
    window = text[max(0, start - 80) : start]
    found = list(_ARTICLE.finditer(window))
    if not found or window[found[-1].end() :].strip(" ,") not in ("", "указанного"):
        return None, None
    m = found[-1]
    return m.group("article"), m.group("part") or m.group("part2")


def extract_refs(paragraphs: list[str]) -> list[NpaRef]:
    refs: list[NpaRef] = []
    for i, text in enumerate(paragraphs):
        taken: list[tuple[int, int]] = []
        for m in _ACT.finditer(text):
            kind = next(k for k, _ in _KIND_WORDS if m.group(k))
            number = m.group("number")
            adopted = parse_date(m.group("date") or m.group("date2") or "")
            # «… № 152-ФЗ «О персональных данных» (в ред. от …)» — редакция после названия.
            edition = _EDITION.search(text[m.end() : m.end() + 160])
            article, part = _article_before(text, m.start())
            refs.append(
                NpaRef(
                    m.group(0),
                    kind,
                    number,
                    adopted,
                    parse_date(edition.group(1)) if edition else None,
                    article,
                    part,
                    i,
                )
            )
            taken.append((m.start(), m.end()))
        for m in _BARE_FZ.finditer(text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            kind = "federal_constitutional_law" if "ФКЗ" in m.group(1) else "federal_law"
            article, part = _article_before(text, m.start())
            edition = _EDITION.search(text[m.end() : m.end() + 60])
            refs.append(
                NpaRef(
                    m.group(0).strip(),
                    kind,
                    m.group(1),
                    None,
                    parse_date(edition.group(1)) if edition else None,
                    article,
                    part,
                    i,
                )
            )
        for m in _GOST.finditer(text):
            number = re.sub(r"^ГОСТ\s+", "", m.group(0))
            refs.append(NpaRef(m.group(0), "gost", number, None, None, None, None, i))
    return refs


@dataclass(frozen=True)
class ActHeader:
    kind: str
    number: str
    adopted_at: date | None
    title: str
    edition_date: date | None


def parse_header(paragraphs: list[str]) -> ActHeader | None:
    """Вид, номер, дата и название акта по первым строкам его текста."""
    head = paragraphs[:15]
    joined = " ".join(head)
    refs = extract_refs([joined])
    if not refs:
        return None
    ref = refs[0]
    adopted = ref.adopted_at
    if adopted is None and (m := re.search(_DATE, joined)):
        adopted = parse_date(m.group(0))
    title = next(
        (p.strip('«»" ') for p in head if re.match(r"^(О|Об)\s", p.strip('«" ')) and len(p) < 400),
        "",
    )
    edition = _EDITION.search(joined)
    return ActHeader(
        ref.kind, ref.number, adopted, title, parse_date(edition.group(1)) if edition else None
    )
