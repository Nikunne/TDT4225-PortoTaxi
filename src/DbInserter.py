"""Import Porto trips into MySQL, or check the CSV with --dry-run."""

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from haversine import haversine


DEFAULT_CSV = Path(__file__).resolve().parents[1] / "data" / "porto.csv"
REQUIRED_COLUMNS = {
    "TRIP_ID", "CALL_TYPE", "ORIGIN_CALL", "ORIGIN_STAND", "TAXI_ID",
    "TIMESTAMP", "DAY_TYPE", "MISSING_DATA", "POLYLINE",
}


@dataclass
class Trip:
    trip_id: int
    values: tuple
    path: str | None
    route_status: str


def prepare_trip(row):
    """Convert one CSV row. Keep unusual trips and the source missing-data flag."""
    trip_id = int(row["TRIP_ID"])
    start_time = datetime.fromtimestamp(int(row["TIMESTAMP"]), timezone.utc).replace(tzinfo=None)
    if row["MISSING_DATA"].lower() not in {"true", "false"}:
        raise ValueError(f"Trip {trip_id}: invalid MISSING_DATA value")
    if row["CALL_TYPE"] not in {"A", "B", "C"} or row["DAY_TYPE"] not in {"A", "B", "C"}:
        raise ValueError(f"Trip {trip_id}: invalid call or day type")

    coords = None
    route_status = "invalid_json"
    try:
        parsed = json.loads(row["POLYLINE"])
    except (json.JSONDecodeError, TypeError):
        pass
    else:
        route_status = "invalid_coordinates"
        if isinstance(parsed, list) and all(
            isinstance(point, list) and len(point) == 2
            and all(type(value) in (int, float) and math.isfinite(value) for value in point)
            and -180 <= point[0] <= 180 and -90 <= point[1] <= 90
            for point in parsed
        ):
            # MySQL's geographic longitude range excludes -180; +180 is equivalent.
            coords = [(180 if lon == -180 else lon, lat) for lon, lat in parsed]
            route_status = "ok" if len(coords) >= 2 else "single_point" if coords else "empty"

    num_points = len(coords) if coords is not None else None
    distance = duration = end_time = start_point = end_point = path = None
    if coords:
        duration = (len(coords) - 1) * 15
        end_time = start_time + timedelta(seconds=duration)
        distance = sum(
            haversine((a[1], a[0]), (b[1], b[0]), unit="m")
            for a, b in zip(coords, coords[1:])
        )
        start_point = f"POINT({coords[0][0]} {coords[0][1]})"
        end_point = f"POINT({coords[-1][0]} {coords[-1][1]})"
        if len(coords) >= 2:
            path = "LINESTRING(" + ",".join(f"{lon} {lat}" for lon, lat in coords) + ")"

    values = (
        trip_id, row["CALL_TYPE"], int(row["ORIGIN_CALL"]) if row["ORIGIN_CALL"] else None,
        int(row["ORIGIN_STAND"]) if row["ORIGIN_STAND"] else None, int(row["TAXI_ID"]),
        start_time, end_time, row["DAY_TYPE"], row["MISSING_DATA"].lower() == "true",
        route_status, distance, duration, num_points, start_point, end_point,
    )
    return Trip(trip_id, values, path, route_status)


class DbInserter:
    def __init__(self, connection=None):
        if connection is None:
            # Dry runs do not need database credentials or a running server.
            from DbConnector import DbConnector
            connection = DbConnector()
        self.connection = connection
        self.db_connection = connection.db_connection
        self.cursor = connection.cursor

    def create_tables(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS trips (
                trip_id BIGINT PRIMARY KEY,
                call_type CHAR(1) NOT NULL,
                origin_call BIGINT NULL,
                origin_stand BIGINT NULL,
                taxi_id BIGINT NOT NULL,
                start_time DATETIME NOT NULL,
                end_time DATETIME NULL,
                day_type CHAR(1) NOT NULL,
                missing_data BOOLEAN NOT NULL,
                route_status VARCHAR(24) NOT NULL,
                trip_length_m DOUBLE NULL,
                duration_s INT NULL,
                num_points INT NULL,
                start_point POINT NULL SRID 4326,
                end_point POINT NULL SRID 4326,
                INDEX trips_taxi_time (taxi_id, start_time),
                INDEX trips_start_time (start_time)
            ) ENGINE=InnoDB
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS trajectories (
                trip_id BIGINT PRIMARY KEY,
                path LINESTRING NULL SRID 4326,
                FOREIGN KEY (trip_id) REFERENCES trips(trip_id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)
        self.db_connection.commit()

    def insert_batch(self, trips):
        """Commit both tables together; existing trips are left unchanged."""
        try:
            placeholders = ",".join(["%s"] * len(trips))
            self.cursor.execute(
                f"SELECT trip_id FROM trips WHERE trip_id IN ({placeholders})",
                tuple(trip.trip_id for trip in trips),
            )
            existing = {row[0] for row in self.cursor.fetchall()}
            new_trips = [trip for trip in trips if trip.trip_id not in existing]
            if new_trips:
                self.cursor.executemany("""
                    INSERT INTO trips (
                        trip_id, call_type, origin_call, origin_stand, taxi_id,
                        start_time, end_time, day_type, missing_data, route_status,
                        trip_length_m, duration_s, num_points, start_point, end_point
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        ST_GeomFromText(%s, 4326, 'axis-order=long-lat'),
                        ST_GeomFromText(%s, 4326, 'axis-order=long-lat')
                    )
                """, [trip.values for trip in new_trips])
                self.cursor.executemany("""
                    INSERT INTO trajectories (trip_id, path)
                    VALUES (%s, ST_GeomFromText(%s, 4326, 'axis-order=long-lat'))
                """, [(trip.trip_id, trip.path) for trip in new_trips])
            self.db_connection.commit()
            return len(new_trips)
        except Exception:
            self.db_connection.rollback()
            raise

    def close(self):
        self.connection.close_connection()


def import_csv(csv_path=DEFAULT_CSV, batch_size=500, limit=None, dry_run=False):
    """Read incrementally. Keep the first occurrence of each TRIP_ID in the file."""
    if batch_size < 1 or (limit is not None and limit < 1):
        raise ValueError("Batch size and limit must be positive")
    # Some routes exceed the csv module's default field size.
    csv.field_size_limit(64 * 1024 * 1024)
    stats = dict(read=0, unique=0, duplicates=0, unusable_routes=0, source_missing=0,
                 inserted=0, existing=0)
    seen = set()
    batch = []
    inserter = None

    def flush():
        if inserter is not None and batch:
            inserted = inserter.insert_batch(batch)
            stats["inserted"] += inserted
            stats["existing"] += len(batch) - inserted
        batch.clear()

    try:
        with Path(csv_path).open(newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")
            if not dry_run:
                inserter = DbInserter()
                inserter.create_tables()
            for row in reader:
                if limit is not None and stats["read"] >= limit:
                    break
                stats["read"] += 1
                try:
                    trip_id = int(row["TRIP_ID"])
                    if trip_id in seen:
                        stats["duplicates"] += 1
                        continue
                    trip = prepare_trip(row)
                except (ValueError, TypeError, OverflowError, KeyError) as exc:
                    raise ValueError(f"CSV record {stats['read']}: {exc}") from exc
                seen.add(trip_id)
                stats["unique"] += 1
                stats["unusable_routes"] += trip.route_status != "ok"
                stats["source_missing"] += trip.values[8]
                batch.append(trip)
                if len(batch) >= batch_size:
                    flush()
                    print(f"Read {stats['read']:,} rows; inserted {stats['inserted']:,}", flush=True)
            flush()
    finally:
        if inserter is not None:
            inserter.close()

    print("Dry run complete." if dry_run else "Import complete.")
    for name, value in stats.items():
        print(f"{name.replace('_', ' ').capitalize()}: {value:,}")
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--limit", type=int, help="Read only the first N CSV records")
    parser.add_argument("--dry-run", action="store_true", help="Preprocess without connecting to MySQL")
    args = parser.parse_args()
    import_csv(args.csv, args.batch_size, args.limit, args.dry_run)


if __name__ == "__main__":
    main()
