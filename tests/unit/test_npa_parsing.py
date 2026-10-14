from datetime import date

from gosdoc.docs_check.checker import quote_in
from gosdoc.docs_check.npa_refs import extract_refs, parse_date

from rag.chunker import chunk_act
from rag.search import rrf


def test_extract_refs_variants() -> None:
    refs = extract_refs(
        [
            "В соответствии с частью 1 статьи 12 Федерального закона от 02.05.2006 № 59-ФЗ «О порядке» (в ред. от 27.07.2010)",
            "согласно ст. 9 Федерального закона от 27 июля 2006 г. N 152-ФЗ и ст. 5 152-ФЗ",
            "постановлением Правительства РФ от 15.06.2009 № 477 и ГОСТ Р 7.0.97-2016",
        ]
    )
    got = [(r.key, r.path, r.edition_date, r.paragraph) for r in refs]
    assert got == [
        ("federal_law:59-ФЗ", "ст. 12 ч. 1", date(2010, 7, 27), 0),
        ("federal_law:152-ФЗ", "ст. 9", None, 1),
        ("federal_law:152-ФЗ", "ст. 5", None, 1),
        ("government_decree:477:2009-06-15", None, None, 2),
        ("gost:Р7.0.97-2016", None, None, 2),
    ]
    assert parse_date("31 февраля 2020") is None


def test_chunker_paths_and_long_parts() -> None:
    text = "Статья 3. Понятия\n1. Первое.\n2. Второе:\n1) пункт;\n2) ещё пункт.\nСтатья 4. Другое\nТекст без частей."
    assert [c.path for c in chunk_act(text)] == [
        "ст. 3 ч. 1",
        "ст. 3 ч. 2",
        "ст. 3 ч. 2 п. 1",
        "ст. 3 ч. 2 п. 2",
        "ст. 4",
    ]
    long = "Статья 1. А\n1. " + " ".join(["Предложение номер один."] * 200)
    pieces = chunk_act(long, max_chars=500)
    assert len(pieces) > 1 and all(p.path == "ст. 1 ч. 1" and len(p.text) <= 500 for p in pieces)


def test_quote_verification_and_rrf() -> None:
    source = "Письменное обращение «рассматривается» в течение 30 дней со дня его регистрации."
    assert quote_in("рассматривается в течение 30 дней", source)
    assert quote_in("РАССМАТРИВАЕТСЯ  в течение\n30 дней", source)
    assert not quote_in("рассматривается в течение 60 дней", source)
    assert not quote_in("30 дней", source)  # слишком короткая цитата ничего не доказывает
    assert rrf([["a", "b", "c"], ["c", "a"]])[:2] == ["a", "c"]
