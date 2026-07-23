from pathlib import Path

import pytest

from mtg_proxies.mpcfill.errors import MpcfillError
from mtg_proxies.mpcfill.order_xml import is_order_xml, parse_order_xml


def _order(fronts: str, quantity: int, extra: str = "<cardback>1CardBackId</cardback>") -> str:
    return f"""<order>
    <details>
        <quantity>{quantity}</quantity>
        <bracket>18</bracket>
        <stock>(S30) Standard Smooth</stock>
        <foil>false</foil>
    </details>
    <fronts>{fronts}</fronts>
    {extra}
</order>"""


def _card(drive_id: str, slots: str, name: str = "Some Card.png") -> str:
    return f"<card><id>{drive_id}</id><slots>{slots}</slots><name>{name}</name><query>q</query></card>"


def test_parse_single_slots(tmp_path: Path) -> None:
    xml = _order(_card("idA", "0", "A.png") + _card("idB", "1", "B.png"), quantity=2)
    path = tmp_path / "order.xml"
    path.write_text(xml)
    assert parse_order_xml(path) == [("idA", "A.png"), ("idB", "B.png")]


def test_parse_multi_slot_expansion_and_ordering(tmp_path: Path) -> None:
    # idA covers slots 0 and 2; idB covers slot 1 — result must be slot-ordered.
    xml = _order(_card("idA", "0,2", "A.png") + _card("idB", "1", "B.png"), quantity=3)
    path = tmp_path / "order.xml"
    path.write_text(xml)
    assert parse_order_xml(path) == [("idA", "A.png"), ("idB", "B.png"), ("idA", "A.png")]


def test_parse_ignores_backs_and_cardback(tmp_path: Path) -> None:
    extra = "<backs>" + _card("idBack", "0", "Back.png") + "</backs><cardback>1XYZ</cardback>"
    xml = _order(_card("idA", "0", "A.png"), quantity=1, extra=extra)
    path = tmp_path / "order.xml"
    path.write_text(xml)
    assert parse_order_xml(path) == [("idA", "A.png")]


def test_parse_uncovered_slot_raises(tmp_path: Path) -> None:
    xml = _order(_card("idA", "0", "A.png"), quantity=2)
    path = tmp_path / "order.xml"
    path.write_text(xml)
    with pytest.raises(MpcfillError, match=r"slot\(s\) \[1\]"):
        parse_order_xml(path)


def test_parse_out_of_range_slot_raises(tmp_path: Path) -> None:
    xml = _order(_card("idA", "0,5", "A.png"), quantity=2)
    path = tmp_path / "order.xml"
    path.write_text(xml)
    with pytest.raises(MpcfillError, match="outside"):
        parse_order_xml(path)


def test_parse_duplicate_slot_claim_raises(tmp_path: Path) -> None:
    xml = _order(_card("idA", "0", "A.png") + _card("idB", "0", "B.png"), quantity=1)
    path = tmp_path / "order.xml"
    path.write_text(xml)
    with pytest.raises(MpcfillError, match="claimed by both"):
        parse_order_xml(path)


def test_parse_missing_id_raises(tmp_path: Path) -> None:
    xml = _order("<card><slots>0</slots><name>A.png</name></card>", quantity=1)
    path = tmp_path / "order.xml"
    path.write_text(xml)
    with pytest.raises(MpcfillError, match="no <id>"):
        parse_order_xml(path)


def test_parse_missing_quantity_raises(tmp_path: Path) -> None:
    path = tmp_path / "order.xml"
    path.write_text("<order><fronts></fronts></order>")
    with pytest.raises(MpcfillError, match="quantity"):
        parse_order_xml(path)


def test_parse_invalid_xml_raises(tmp_path: Path) -> None:
    path = tmp_path / "order.xml"
    path.write_text("1 Murder\n1 Beast Within\n")
    with pytest.raises(MpcfillError, match="not a valid MPC Autofill order"):
        parse_order_xml(path)


def test_is_order_xml(tmp_path: Path) -> None:
    xml_file = tmp_path / "cards.XML"
    xml_file.write_text("<order/>")
    txt_file = tmp_path / "deck.txt"
    txt_file.write_text("1 Murder\n")
    assert is_order_xml(str(xml_file))  # suffix check is case-insensitive
    assert not is_order_xml(str(txt_file))
    assert not is_order_xml(str(tmp_path / "missing.xml"))
    assert not is_order_xml("manastack:12345")
