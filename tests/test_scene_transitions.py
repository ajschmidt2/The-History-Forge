from src.ui.tabs.scenes import _coerce_transition_types


def test_coerce_transition_types_normalizes_and_pads() -> None:
    assert _coerce_transition_types(["WipeLeft", "", None], needed=4) == [
        "wipeleft",
        "fade",
        "fade",
        "fade",
    ]


def test_coerce_transition_types_rejects_unknown_values() -> None:
    assert _coerce_transition_types(["spin", "fadeblack"], needed=2) == ["fade", "fadeblack"]
