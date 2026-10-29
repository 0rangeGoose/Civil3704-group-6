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
