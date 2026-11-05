from engine.analysis.export import KINDS


def test_three_exports():
    # streets.csv went with the street layers (5 Nov 2026)
    assert KINDS == ("vehicles", "stop_visits", "trips")
