from engine.normalise.proto import pb
from engine.normalise.trip_updates import build_trip_update_index


def test_extracts_delay_from_first_stop_time_update_with_data():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "e1"
    e.trip_update.trip.trip_id = "T1"
    stu = e.trip_update.stop_time_update.add()
    stu.arrival.delay = 245

    index = build_trip_update_index(feed.SerializeToString())

    assert index["T1"].next_stop_delay_seconds == 245
    assert index["T1"].cancelled is False


def test_skips_stop_time_updates_with_no_delay_and_uses_next_one():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "e1"
    e.trip_update.trip.trip_id = "T1"
    e.trip_update.stop_time_update.add()  # no arrival/departure at all
    stu2 = e.trip_update.stop_time_update.add()
    stu2.departure.delay = -30

    index = build_trip_update_index(feed.SerializeToString())

    assert index["T1"].next_stop_delay_seconds == -30


def _trip_with_stop_delays(*stops):
    """stops: (stop_id, delay) or (stop_id, delay, schedule_relationship)."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "e1"
    e.trip_update.trip.trip_id = "T1"
    for stop in stops:
        stu = e.trip_update.stop_time_update.add()
        stu.stop_id = stop[0]
        stu.arrival.delay = stop[1]
        if len(stop) > 2:
            stu.schedule_relationship = stop[2]
    return build_trip_update_index(feed.SerializeToString())["T1"]


def test_all_zero_trip_is_a_placeholder_not_on_time():
    info = _trip_with_stop_delays(("S1", 0), ("S2", 0), ("S3", 0))

    assert info.next_stop_delay_seconds is None
    assert info.next_stop_id == "S1"  # where it's heading is still real


def test_zero_next_stop_is_real_when_later_stops_have_a_delay():
    info = _trip_with_stop_delays(("S1", 0), ("S2", 30))

    assert info.next_stop_delay_seconds == 0


def test_no_data_and_skipped_stops_are_ignored():
    stu = pb.TripUpdate.StopTimeUpdate
    info = _trip_with_stop_delays(("S1", 0, stu.NO_DATA), ("S2", 0, stu.SKIPPED), ("S3", 120))

    assert info.next_stop_delay_seconds == 120
    assert info.next_stop_id == "S3"


def test_trip_with_only_no_data_stops_has_no_delay():
    info = _trip_with_stop_delays(("S1", 0, pb.TripUpdate.StopTimeUpdate.NO_DATA))

    assert info.next_stop_delay_seconds is None
    assert info.next_stop_id is None


def test_stops_already_departed_are_skipped_for_feeds_that_list_the_whole_trip():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 10_000
    e = feed.entity.add()
    e.id = "e1"
    e.trip_update.trip.trip_id = "T1"
    for stop_id, delay, departs_at in [("ORIGIN", 0, 9_000), ("PASSED", 40, 9_600), ("NEXT", 75, 10_060), ("LATER", 90, 10_300)]:
        stu = e.trip_update.stop_time_update.add()
        stu.stop_id = stop_id
        stu.departure.delay = delay
        stu.departure.time = departs_at

    info = build_trip_update_index(feed.SerializeToString())["T1"]

    assert info.next_stop_id == "NEXT"
    assert info.next_stop_delay_seconds == 75


def test_origin_zero_does_not_make_a_real_trip_look_like_a_placeholder():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 10_000
    e = feed.entity.add()
    e.id = "e1"
    e.trip_update.trip.trip_id = "T1"
    for stop_id, delay, departs_at in [("ORIGIN", 0, 9_000), ("NEXT", 0, 10_100), ("LATER", 0, 10_400)]:
        stu = e.trip_update.stop_time_update.add()
        stu.stop_id = stop_id
        stu.departure.delay = delay
        stu.departure.time = departs_at

    info = build_trip_update_index(feed.SerializeToString())["T1"]

    # Upcoming stops are all exactly 0 -> still a placeholder, judged on upcoming stops only.
    assert info.next_stop_delay_seconds is None
    assert info.next_stop_id == "NEXT"


def test_falls_back_to_trip_level_delay_when_no_stop_time_updates():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "e1"
    e.trip_update.trip.trip_id = "T1"
    e.trip_update.delay = 90

    index = build_trip_update_index(feed.SerializeToString())

    assert index["T1"].next_stop_delay_seconds == 90


def test_cancelled_trip_is_flagged():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "e1"
    e.trip_update.trip.trip_id = "T1"
    e.trip_update.trip.schedule_relationship = pb.TripDescriptor.CANCELED

    index = build_trip_update_index(feed.SerializeToString())

    assert index["T1"].cancelled is True
    assert index["T1"].next_stop_delay_seconds is None


def test_extracts_next_stop_id_from_the_same_entry_as_the_delay():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "e1"
    e.trip_update.trip.trip_id = "T1"
    skipped = e.trip_update.stop_time_update.add()  # no delay -> skipped, its stop_id ignored
    skipped.stop_id = "WRONG_STOP"
    stu = e.trip_update.stop_time_update.add()
    stu.stop_id = "204421"
    stu.arrival.delay = 31

    index = build_trip_update_index(feed.SerializeToString())

    assert index["T1"].next_stop_id == "204421"
    assert index["T1"].next_stop_delay_seconds == 31


def test_next_stop_id_is_none_when_falling_back_to_trip_level_delay():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "e1"
    e.trip_update.trip.trip_id = "T1"
    e.trip_update.delay = 90

    index = build_trip_update_index(feed.SerializeToString())

    assert index["T1"].next_stop_id is None


def test_entities_without_trip_update_are_ignored():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "e1"
    e.vehicle.position.latitude = -33.86
    e.vehicle.position.longitude = 151.2

    index = build_trip_update_index(feed.SerializeToString())

    assert index == {}
