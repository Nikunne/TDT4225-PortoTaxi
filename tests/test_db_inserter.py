import contextlib
import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from DbInserter import DbInserter, import_csv, prepare_trip


def row(**changes):
    values = {
        "TRIP_ID": "1372636858620000589", "CALL_TYPE": "C",
        "ORIGIN_CALL": "", "ORIGIN_STAND": "", "TAXI_ID": "20000589",
        "TIMESTAMP": "1372636858", "DAY_TYPE": "A", "MISSING_DATA": "False",
        "POLYLINE": "[[0,60],[1,60],[1,60]]",
    }
    return values | changes


class PrepareTripTests(unittest.TestCase):
    def test_distance_units_coordinate_order_and_elapsed_time(self):
        trip = prepare_trip(row())
        self.assertAlmostEqual(trip.values[10], 55_597, delta=10)
        self.assertEqual(trip.values[11:13], (30, 3))
        self.assertEqual((trip.values[6] - trip.values[5]).total_seconds(), 30)
        self.assertEqual(trip.values[2:4], (None, None))
        self.assertEqual(trip.values[5].isoformat(), "2013-07-01T00:00:58")
        self.assertEqual(trip.path, "LINESTRING(0 60,1 60,1 60)")

    def test_missing_flag_is_preserved_for_complete_looking_route(self):
        trip = prepare_trip(row(MISSING_DATA="True"))
        self.assertTrue(trip.values[8])
        self.assertEqual(trip.route_status, "ok")

    def test_empty_single_and_two_point_trips_are_kept(self):
        empty = prepare_trip(row(POLYLINE="[]"))
        self.assertEqual(empty.route_status, "empty")
        self.assertEqual(empty.values[12], 0)
        self.assertIsNone(empty.values[11])
        self.assertIsNone(empty.path)
        single = prepare_trip(row(POLYLINE="[[0,60]]"))
        self.assertEqual(single.values[10:13], (0, 0, 1))
        self.assertIsNone(single.path)
        two = prepare_trip(row(POLYLINE="[[0,60],[1,60]]"))
        self.assertEqual(two.values[12], 2)
        self.assertIsNotNone(two.path)

    def test_bad_routes_do_not_get_fabricated_distances(self):
        for polyline in ["bad JSON", "null", "[[1,100]]", "[[NaN,1]]", "[[true,1]]", "[[1]]"]:
            with self.subTest(polyline=polyline):
                trip = prepare_trip(row(POLYLINE=polyline))
                self.assertTrue(trip.route_status.startswith("invalid_"))
                self.assertIsNone(trip.values[10])
                self.assertIsNone(trip.path)

    def test_bad_metadata_stops_import(self):
        with self.assertRaises(ValueError):
            prepare_trip(row(MISSING_DATA="unknown"))


class ImportTests(unittest.TestCase):
    def test_existing_trips_are_skipped(self):
        connection = MagicMock()
        trip = prepare_trip(row())
        connection.cursor.fetchall.return_value = [(trip.trip_id,)]
        self.assertEqual(DbInserter(connection).insert_batch([trip]), 0)
        connection.cursor.executemany.assert_not_called()

    def test_trajectory_failure_rolls_back_both_tables(self):
        connection = MagicMock()
        connection.cursor.fetchall.return_value = []
        connection.cursor.executemany.side_effect = [None, RuntimeError("trajectory failure")]
        with self.assertRaisesRegex(RuntimeError, "trajectory failure"):
            DbInserter(connection).insert_batch([prepare_trip(row())])
        connection.db_connection.rollback.assert_called_once()
        connection.db_connection.commit.assert_not_called()

    def test_dry_run_deduplicates_across_batches_without_connecting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trips.csv"
            with path.open("w", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=row().keys())
                writer.writeheader()
                writer.writerows([row(), row(TRIP_ID="2", POLYLINE="[]"), row()])
            with patch("DbInserter.DbInserter") as connector, contextlib.redirect_stdout(io.StringIO()):
                stats = import_csv(path, batch_size=1, dry_run=True)
            connector.assert_not_called()
            self.assertEqual(stats["read"], 3)
            self.assertEqual(stats["unique"], 2)
            self.assertEqual(stats["duplicates"], 1)
            self.assertEqual(stats["unusable_routes"], 1)
            self.assertEqual(stats["inserted"], 0)


if __name__ == "__main__":
    unittest.main()
