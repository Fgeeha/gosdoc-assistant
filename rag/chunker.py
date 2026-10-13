"""Чанкинг текста НПА по структуре: статья → часть → пункт.

Путь чанка — как в ссылках: «ст. 12 ч. 1 п. 3». Если части слишком длинные для
контекста модели, они режутся по предложениям с сохранением пути.
"""

import re
from dataclasses import dataclass

_ARTICLE = re.compile(r"^\s*Статья\s+(\d+(?:\.\d+)*)\.?\s*(.*)$")
_PART = re.compile(r"^\s*(\d+(?:\.\d+)*)\.\s+(\S.*)$")  # «1. Текст» — часть статьи
_ITEM = re.compile(r"^\s*(\d+(?:\.\d+)*)\)\s+(\S.*)$")  # «1) текст» — пункт части
_CHAPTER = re.compile(r"^\s*(Глава|Раздел)\s+[\dIVXLC]+", re.IGNORECASE)


@dataclass(frozen=True)
class Chunk:
    path: str  # «ст. 12 ч. 1»
    article: str  # «12»
    text: str  # текст фрагмента (без заголовка статьи)
    heading: str  # «Статья 12. Сроки рассмотрения письменного обращения»


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts, current = [], ""
    for sentence in re.split(r"(?<=[.;:])\s+", text):
        if current and len(current) + len(sentence) + 1 > max_chars:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    return [*parts, current] if current else parts


def chunk_act(text: str, max_chars: int = 2000) -> list[Chunk]:
    """Текст без статей (постановления, приказы) режется на пункты «1.»."""
    chunks: list[Chunk] = []
    article, heading, part = "", "", ""
    buffer: list[str] = []
    path = ""

    def flush() -> None:
        body = " ".join(buffer).strip()
        if body:
            for piece in _split_long(body, max_chars):
                chunks.append(Chunk(path or "преамбула", article, piece, heading))
        buffer.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line or _CHAPTER.match(line):
            continue
        if m := _ARTICLE.match(line):
            flush()
            article, part = m.group(1), ""
            heading = line
            path = f"ст. {article}"
            continue
        if m := _PART.match(line):
            flush()
            part = m.group(1)
            path = f"ст. {article} ч. {part}" if article else f"п. {part}"
            buffer.append(m.group(2))
            continue
        if (m := _ITEM.match(line)) and path:
            flush()
            base = (
                f"ст. {article} ч. {part}"
                if article and part
                else (f"ст. {article}" if article else f"п. {part}")
            )
            path = f"{base} п. {m.group(1)}"
            buffer.append(m.group(2))
            continue
        buffer.append(line)
    flush()
    return chunks
