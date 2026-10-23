import csv
import gzip

import pytest

from engine.demand.boam import LOAD_BANDS, LOST_BANDS, band, occupancy_midpoint, section_samples
from engine.publish.control import ControlError, ControlState

COLUMNS = ["SERVICE_DATE", "ROUTE", "ROUTE_VARIANT", "TRIP_ID", "DIRECTION", "TRANSIT_STOP_SEQUENCE",
           "SCHD_ARRIVE_TIME", "ACTUAL_ARRIVE_TIME", "TRANSIT_STOP", "LATITUDE", "LONGITUDE",
           "SEATED_CAPACITY", "TOTAL_CAPACITY", "OCCUPANCY_RANGE"]


def _day(path, rows):
    with gzip.open(path, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for trip, seq, sched, actual, stop, occ in rows:
            w.writerow(["", "333", "333-1", trip, "Inbound", seq, sched, actual, stop, -33.87, 151.21, 40, 80, occ])


def test_occupancy_midpoints_and_bands():
    assert occupancy_midpoint("0-20") == 10 and occupancy_midpoint("21-40") == 30.5
    assert occupancy_midpoint("") is None
    assert band(0.05, LOAD_BANDS) == 0 and band(0.7, LOAD_BANDS) == 4
    assert band(-4, LOST_BANDS) == 0 and band(20, LOST_BANDS) == 4


def test_sections_measure_load_leaving_a_and_time_lost_from_a_to_b(tmp_path):
    rows = [  # one AM trip: 60s late at A, 150s late at B -> lost 90s on A>B
        ("T1", 1, "07:30:00", "07:31:00", "A", "41-60"),
        ("T1", 2, "07:33:00", "07:35:30", "B", "21-40"),
        ("T1", 4, "07:40:00", "07:40:00", "D", "0-20"),   # sequence gap: B>D isn't a section
        ("T2", 1, "12:00:00", "12:00:00", "A", "0-20"),   # midday: no period
        ("T2", 2, "12:03:00", "12:03:00", "B", "0-20"),
    ]
    _day(tmp_path / "BOAM_20260929.csv.gz", rows)   # a Tuesday
    _day(tmp_path / "BOAM_20260927.csv.gz", rows)   # a Sunday: left out
    samples, stops, days = section_samples(sorted(str(p) for p in tmp_path.iterdir()))
    assert days == ["20260929"]
    assert set(samples) == {("A", "B", "am")}
    s = samples[("A", "B", "am")]
    assert s["load"] == [pytest.approx(50.5 / 80)]
    assert s["lost_s"] == [90]


def test_boam_period_control():
    c = ControlState()
    assert c.state["boam_period"] == "am"
    c.apply({"boam_period": "pm", "line_layer": "boam_lost"})
    with pytest.raises(ControlError):
        c.apply({"boam_period": "midday"})
