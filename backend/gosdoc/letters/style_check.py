"""Проверка официально-делового стиля: словарь оборотов и длина предложений.

Только подсказки исполнителю: текст не меняется автоматически.
"""

import re
from dataclasses import dataclass

MAX_SENTENCE_WORDS = 40

# Разговорные и недопустимые в служебной переписке обороты → что предложить.
_PHRASES: dict[str, str] = {
    "щас": "сейчас",
    "короче": "удалить",
    "типа": "например / удалить",
    "как бы": "удалить",
    "на данный момент времени": "в настоящее время",
    "в данный момент времени": "в настоящее время",
    "доводим до вашего сведения": "сообщаем",
    "ставим вас в известность": "сообщаем",
    "в связи с тем что": "так как / в связи с",
    "огромное спасибо": "благодарим",
    "спасибо": "благодарим",
    "к сожалению": "удалить (эмоциональная оценка)",
    "очень": "удалить (усилительная частица)",
    "просто": "удалить",
    "ок": "согласовано",
    "пожалуйста": "просим",
    "в кратчайшие сроки": "указать конкретный срок",
    "в ближайшее время": "указать конкретный срок",
    "и т.д.": "перечислить полностью",
    "и т. д.": "перечислить полностью",
}
_PATTERNS = [
    (re.compile(rf"(?<![а-яёa-z]){re.escape(p)}(?![а-яёa-z])", re.IGNORECASE), p, s)
    for p, s in _PHRASES.items()
]


@dataclass(frozen=True)
class StyleIssue:
    paragraph: int  # номер абзаца с нуля
    fragment: str
    message: str


def check_style(paragraphs: list[str]) -> list[StyleIssue]:
    issues: list[StyleIssue] = []
    for i, text in enumerate(paragraphs):
        for pattern, phrase, suggestion in _PATTERNS:
            for m in pattern.finditer(text):
                issues.append(
                    StyleIssue(
                        i, m.group(0), f"«{phrase}» — разговорный оборот, лучше: {suggestion}"
                    )
                )
        if "!" in text:
            issues.append(StyleIssue(i, "!", "восклицательный знак допустим только в обращении"))
        if re.search(r"\.{3}|…", text):
            issues.append(StyleIssue(i, "…", "многоточие в служебном письме не используется"))
        for sentence in re.split(r"(?<=[.?!;])\s+", text):
            words = len(sentence.split())
            if words > MAX_SENTENCE_WORDS:
                issues.append(
                    StyleIssue(
                        i, sentence[:60] + "…", f"длинное предложение ({words} слов), разделите"
                    )
                )
    return issues
