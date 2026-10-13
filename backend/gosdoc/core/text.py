"""Извлечение текста из документов: DOCX, PDF с текстовым слоем, TXT.

Сканы (PDF без текста) распознаются OCR в модуле проверки документов (этап 5);
здесь для них возвращается ошибка с понятным текстом.
"""

import io
from pathlib import PurePath

import pdfplumber
from docx import Document

FORMATS = (".docx", ".pdf", ".txt")


class TextExtractionError(ValueError):
    pass


def extract_text(data: bytes, filename: str) -> str:
    ext = PurePath(filename).suffix.lower()
    try:
        if ext == ".docx":
            doc = Document(io.BytesIO(data))
            cells = [c.text for t in doc.tables for row in t.rows for c in row.cells]
            return "\n".join([p.text for p in doc.paragraphs] + cells).strip()
        if ext == ".pdf":
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
            if not text:
                raise TextExtractionError(
                    "В PDF нет текстового слоя (скан) — загрузите DOCX или PDF с текстом"
                )
            return text
        if ext == ".txt":
            try:
                return data.decode("utf-8-sig").strip()
            except UnicodeDecodeError:
                return data.decode("cp1251").strip()
    except TextExtractionError:
        raise
    except Exception as e:  # повреждённый файл любого формата
        raise TextExtractionError(f"Не удалось прочитать документ: {type(e).__name__}") from e
    raise TextExtractionError(
        f"Формат {ext or 'без расширения'} не поддерживается: {', '.join(FORMATS)}"
    )
