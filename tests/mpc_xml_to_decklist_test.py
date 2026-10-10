import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "mpc_xml_to_decklist.py"


def _load():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("mpc_xml_to_decklist", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("Sol Ring.png", "Sol Ring"),
        ("Preordain [M11] {70}.jpg", "Preordain"),
        ("Season of Growth (M20 191).jpg", "Season of Growth"),
        ("Opt (Ancient John Howe).jpg", "Opt"),
        ("Hinterland Harbor (Normal) [ISD] {241}.jpg", "Hinterland Harbor"),
        ("Dreamroot Cascade(1).png", "Dreamroot Cascade"),
        ("Lifecrafter's Bestiary.JPEG", "Lifecrafter's Bestiary"),
        ("Arwen Undómiel.jpg", "Arwen Undómiel"),
    ],
)
def test_card_name_strips_mpc_autofill_decorations(file_name: str, expected: str) -> None:
    assert _load().card_name(file_name) == expected


def test_to_decklist_merges_shared_renders_and_keeps_first_slot_order(tmp_path: Path) -> None:
    order = tmp_path / "order.xml"
    order.write_text(
        "<order><details><quantity>3</quantity></details><fronts>"
        "<card><id>idB</id><slots>1</slots><name>Opt.jpg</name></card>"
        "<card><id>idA</id><slots>0,2</slots><name>Sol Ring [C21] {263}.png</name></card>"
        "</fronts></order>"
    )

    assert _load().to_decklist(order, scaled=True) == [
        "2 Sol Ring #mpcfill --identifier idA --scaled",
        "1 Opt #mpcfill --identifier idB --scaled",
    ]
