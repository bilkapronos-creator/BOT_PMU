"""Détection paris buteurs Winamax (betFilterId / betFilterName)."""

from parser_winamax import _collect_buteur_rows, _is_scorer_market_bet


def test_scorer_bet_by_filter_name():
    bet = {
        "betFilterName": "Buteur",
        "betTypeName": "Buteur du match",
        "outcomes": [1],
    }
    assert _is_scorer_market_bet(bet) is True


def test_scorer_bet_by_filter_id_26():
    bet = {
        "betFilterId": 26,
        "betTypeName": "Buteur du match",
        "outcomes": [1],
    }
    assert _is_scorer_market_bet(bet) is True


def test_scorer_bet_rejects_double_chance():
    bet = {
        "betFilterName": "Double chance",
        "betTypeName": "Buteur double chance",
        "outcomes": [1],
    }
    assert _is_scorer_market_bet(bet) is False


def test_collect_buteur_rows_from_outcome_label():
    match_bets = [
        {
            "betFilterName": "Buteur",
            "betTypeName": "Buteur du match",
            "matchId": "99",
            "outcomes": [101, 102],
        }
    ]
    outcomes = {
        101: {"label": "K. Mbappé", "id": 101},
        102: {"label": "A. Diallo", "id": 102},
    }
    odds = {101: 4.5, 102: 6.0}
    rows = _collect_buteur_rows(match_bets, outcomes, odds, 5)
    assert len(rows) == 2
    assert rows[0]["joueur"] == "K. Mbappé"
    assert rows[0]["cote"] == 4.5
