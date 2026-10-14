from gosdoc.core.logging import mask_obj, mask_pdn


def test_masks_all_pdn_kinds() -> None:
    text = (
        "СНИЛС 112-233-445 95, ИНН 7707083893, ИНН ФЛ 500100732259, паспорт 45 08 123456, "
        "тел. +7 (912) 345-67-89, почта ivanov@mail.fgau.local, карта 4276 1600 1234 5678"
    )
    masked = mask_pdn(text)
    for secret in (
        "112-233-445 95",
        "7707083893",
        "500100732259",
        "123456",
        "345-67-89",
        "ivanov@",
        "4276",
    ):
        assert secret not in masked
    for label in ("<snils>", "<inn>", "<passport>", "<phone>", "<email>", "<card>"):
        assert label in masked


def test_keeps_regular_text_and_dates() -> None:
    text = "Федеральный закон от 02.05.2006 № 59-ФЗ, статья 12, срок 30 дней"
    assert mask_pdn(text) == text


def test_mask_obj_is_recursive() -> None:
    assert mask_obj({"a": ["ИНН 7707083893"], "n": 5}) == {"a": ["ИНН <inn>"], "n": 5}
