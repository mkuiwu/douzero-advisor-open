from collections import Counter
from pathlib import Path

from douzero_advisor.cards import FULL_DECK, Rank, from_env_cards, to_env_cards


def test_card_mapping_round_trip_and_deck_shape() -> None:
    ranks = [rank.value for rank in Rank]
    assert from_env_cards(to_env_cards(ranks)) == ranks
    counts = Counter(FULL_DECK)
    assert len(FULL_DECK) == 54
    assert counts[20] == counts[30] == 1
    assert all(counts[value] == 4 for value in range(3, 15))
    assert counts[17] == 4


def test_imported_assets_have_attribution() -> None:
    root = Path(__file__).parents[1]
    attribution = (root / "assets" / "ATTRIBUTION.md").read_text("utf-8")
    assert "513dc14cff293f821c816db3b727204cddfdf88a" in attribution
    assert "85afd773abd01c411f543d6ade5b99a4fde327d2" in attribution
    assert (root / "LICENSE").is_file()
    assert (root / "assets" / "templates" / "LICENSE").is_file()
    assert (root / "assets" / "templates" / "my" / "my_D.png").is_file()
