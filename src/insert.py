"""Clean the Porto taxi CSV and insert the cleaned trips into MySQL."""

from haversine import haversine
from pathlib import Path

import ast
import json
import argparse

import pandas as pd

from DbConnector import DbConnector

DATA_PATH = Path(__file__).resolve().parent / "data" / "porto.csv"
BATCH_SIZE = 2_000
GPS_BATCH_SIZE = 100_000    # Keeps each INSERT well below MySQL's 64 MB max_allowed_packet
GPS_INTERVAL_SECONDS = 15
REQUIRED_COLUMNS = {
    "TRIP_ID",
    "CALL_TYPE",
    "TAXI_ID",
    "TIMESTAMP",
    "POLYLINE",
}

class DataInserter:
    """Load, clean, and insert the Porto taxi data into the trips and gps_points tables."""

    def __init__(self, csv_path=DATA_PATH, batch_size=BATCH_SIZE):
        """Connect to the database and store the insertion settings.

        Args:
            csv_path (Path): Path to the CSV file containing the Porto taxi data.
            batch_size (int): Maximum number of trips inserted per batch.
        """
        self.csv_path = csv_path
        self.batch_size = batch_size
        self.connection = DbConnector()
        self.db_connection = self.connection.db_connection
        self.cursor = self.connection.cursor

    def run(self) -> None:
        """Run every step needed to clean and insert the data, in order.

        Returns:
            None
        """
        df = self.load_data()
        df = self.parse_polylines(df)
        df = self.remove_duplicate_trips(df)
        df = self.remove_invalid_polylines(df)
        df = self.add_derived_fields(df)
        df = self.remove_outliers(df)
        print(f"Rows ready for insertion: {len(df):,}")
        self.drop_tables()
        self.create_tables()
        self.insert_data(df)

    def load_data(self) -> pd.DataFrame:
        """Load the columns needed for insertion from the CSV file.

        Returns:
            pd.DataFrame: The raw Porto taxi data with only the required columns.
        """
        df = pd.read_csv(self.csv_path, usecols=sorted(REQUIRED_COLUMNS))
        print(f"Loaded {len(df):,} rows")
        return df

    @staticmethod
    def parse_polyline(value) -> list[list[float]] | None:
        """Parse a POLYLINE value into a list of coordinates.

        Args:
            value (str | list): The raw POLYLINE value, as a JSON string or a list of coordinates.
        Returns:
            list[list[float]] | None: The [longitude, latitude] points, or None if the value is unusable.
        """
        if pd.isna(value) or value == "":
            return None

        try:
            parsed = json.loads(value) if isinstance(value, str) else ast.literal_eval(value)
        except (TypeError, ValueError, SyntaxError, json.JSONDecodeError):
            return None

        if not isinstance(parsed, list):
            return None

        for point in parsed:
            if (
                not isinstance(point, (list, tuple))
                or len(point) != 2
                or not all(isinstance(value, (int, float)) for value in point)
            ):
                return None

        return [[float(point[0]), float(point[1])] for point in parsed]

    def parse_polylines(self, df) -> pd.DataFrame:
        """Parse every POLYLINE value into a list of coordinates.

        Unusable values become None, so they can be removed after duplicates are handled.

        Args:
            df (pd.DataFrame): The Porto taxi data with raw POLYLINE values.
        Returns:
            pd.DataFrame: The data with parsed POLYLINE values.
        """
        df = df.copy()
        df["POLYLINE"] = df["POLYLINE"].map(self.parse_polyline)
        return df

    @staticmethod
    def remove_invalid_polylines(df) -> pd.DataFrame:
        """Remove trips with an empty or invalid POLYLINE.

        Args:
            df (pd.DataFrame): The Porto taxi data with parsed POLYLINE values.
        Returns:
            pd.DataFrame: The data with empty and invalid trajectories removed.
        """
        before = len(df)
        invalid_or_empty = df["POLYLINE"].map(
            lambda polyline: polyline is None or len(polyline) == 0
        )
        removed = int(invalid_or_empty.sum())
        df = df.loc[~invalid_or_empty].copy()
        print(f"Cleaning empty/invalid POLYLINE rows: removed {removed:,} of {before:,}")
        return df

    @staticmethod
    def remove_duplicate_trips(df) -> pd.DataFrame:
        """Keep the row with the longest trajectory for each TRIP_ID.

        Runs before empty and invalid trajectories are removed, so every duplicate
        row in the raw data is counted. An invalid POLYLINE counts as shorter than
        an empty one, so a valid copy is always kept when one exists.

        Args:
            df (pd.DataFrame): The Porto taxi data with parsed POLYLINE values.
        Returns:
            pd.DataFrame: The data with duplicate TRIP_ID rows removed.
        """
        before = len(df)
        duplicate_rows = int(df["TRIP_ID"].duplicated(keep=False).sum())
        df = df.copy()
        df["_trajectory_length"] = df["POLYLINE"].map(
            lambda polyline: -1 if polyline is None else len(polyline)
        )
        df = (
            df.sort_values("_trajectory_length", ascending=False, kind="mergesort")
            .drop_duplicates(subset="TRIP_ID", keep="first")
            .drop(columns="_trajectory_length")
            .sort_index()
        )
        removed = before - len(df)
        print(
            "Cleaning duplicate TRIP_ID rows: "
            f"removed {removed:,} rows ({duplicate_rows:,} rows belonged to duplicate groups)"
        )
        return df

    def add_derived_fields(self, df) -> pd.DataFrame:
        """Add the local start time, duration, and distance of each trip.

        Args:
            df (pd.DataFrame): The Porto taxi data with parsed POLYLINE values.
        Returns:
            pd.DataFrame: The data with start_time, duration_s, and distance_km columns added.
        """
        df = df.copy()
        datetime_local = (
            pd.to_datetime(df["TIMESTAMP"], unit="s", utc=True)
            .dt.tz_convert("Europe/Lisbon")
        )
        df["start_time"] = datetime_local.dt.tz_localize(None)
        df["duration_s"] = (df["POLYLINE"].map(len) - 1) * GPS_INTERVAL_SECONDS
        df["distance_km"] = df["POLYLINE"].map(self.calculate_distance)
        return df

    @staticmethod
    def calculate_distance(polyline) -> float:
        """Calculate the recorded trajectory distance with the Haversine formula.

        Args:
            polyline (list[list[float]]): The [longitude, latitude] points of the trajectory.
        Returns:
            float: The total distance of the trajectory in kilometres.
        """
        return sum(
            haversine((point_a[1], point_a[0]), (point_b[1], point_b[0]))
            for point_a, point_b in zip(polyline, polyline[1:])
        )

    @staticmethod
    def remove_outliers(df) -> pd.DataFrame:
        """Remove outlier trips based on duration, distance, and average speed.

        Not active yet: the input is returned unchanged.

        Args:
            df (pd.DataFrame): The Porto taxi data with the derived fields from add_derived_fields.
        Returns:
            pd.DataFrame: The data with outlier trips removed.
        """
        # TODO: Remove outliers using the IQR analysis from section 13 in EDA2.ipynb
        print("Removing outliers: not implemented yet, removed 0 rows")
        return df

    def drop_tables(self) -> None:
        """Drop the trips and gps_points tables so every run starts from empty tables.

        gps_points is dropped first because it references trips with a foreign key.

        Returns:
            None
        """
        self.cursor.execute("DROP TABLE IF EXISTS gps_points")
        self.cursor.execute("DROP TABLE IF EXISTS trips")
        self.db_connection.commit()
        print("Dropped existing trips and gps_points tables")

    def create_tables(self) -> None:
        """Create the trips and gps_points tables if they do not exist.

        Returns:
            None
        """
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS trips (
                trip_id BIGINT PRIMARY KEY,
                taxi_id BIGINT NOT NULL,
                call_type CHAR(1) NOT NULL,
                start_time DATETIME NOT NULL,
                duration_s INT NOT NULL,
                distance_km DOUBLE NOT NULL,

                INDEX idx_taxi_id (taxi_id),
                INDEX idx_start_time (start_time),
                INDEX idx_taxi_start (taxi_id, start_time)
            ) ENGINE=InnoDB
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS gps_points (
                trip_id BIGINT NOT NULL,
                point_order INT NOT NULL,
                longitude DOUBLE NOT NULL,
                latitude DOUBLE NOT NULL,

                PRIMARY KEY (trip_id, point_order),
                FOREIGN KEY (trip_id)
                    REFERENCES trips(trip_id)
                    ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        self.db_connection.commit()

    def insert_data(self, df) -> None:
        """Insert cleaned trips and their GPS points in batches.

        A batch is sent when it reaches batch_size trips or GPS_BATCH_SIZE GPS
        points, whichever comes first, so long trips never exceed MySQL's packet
        limit. Each batch inserts the trips first, then their GPS points, so the
        foreign key from gps_points to trips is always satisfied.

        Args:
            df (pd.DataFrame): The cleaned Porto taxi data.
        Returns:
            None
        """
        trip_batch = []
        gps_batch = []
        inserted_trips = 0
        inserted_points = 0

        for row in df.itertuples(index=False):
            trip_id = int(row.TRIP_ID)
            trip_batch.append((
                trip_id,
                int(row.TAXI_ID),
                row.CALL_TYPE,
                row.start_time.to_pydatetime(),
                int(row.duration_s),
                float(row.distance_km),
            ))
            gps_batch.extend(
                (trip_id, point_order, longitude, latitude)
                for point_order, (longitude, latitude) in enumerate(row.POLYLINE)
            )
            if len(trip_batch) >= self.batch_size or len(gps_batch) >= GPS_BATCH_SIZE:
                self.insert_batch(trip_batch, gps_batch)
                inserted_trips += len(trip_batch)
                inserted_points += len(gps_batch)
                trip_batch.clear()
                gps_batch.clear()
                print(f"Inserted {inserted_trips:,} trips and {inserted_points:,} GPS points", end="\r")

        if trip_batch:
            self.insert_batch(trip_batch, gps_batch)
            inserted_trips += len(trip_batch)
            inserted_points += len(gps_batch)

        print(f"\nInserted or refreshed {inserted_trips:,} trips and {inserted_points:,} GPS points")

    def insert_batch(self, trip_batch, gps_batch) -> None:
        """Insert one batch of trips and their GPS points, then commit.

        Args:
            trip_batch (list[tuple]): Rows for the trips table.
            gps_batch (list[tuple]): Rows for the gps_points table belonging to the trips in trip_batch.
        Returns:
            None
        """
        self.cursor.executemany("""
            INSERT INTO trips (
                trip_id, taxi_id, call_type, start_time, duration_s, distance_km
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                trip_id = VALUES(trip_id)
        """, trip_batch)
        self.cursor.executemany("""
            INSERT INTO gps_points (
                trip_id, point_order, longitude, latitude
            ) VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                trip_id = VALUES(trip_id)
        """, gps_batch)
        self.db_connection.commit()


def main():
    """Parse the CSV path argument, then clean and insert the data.

    Returns:
        None
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DATA_PATH)
    args = parser.parse_args()

    inserter = None
    try:
        inserter = DataInserter(csv_path=args.csv)
        inserter.run()
    except Exception as e:
        print("ERROR: Failed to insert data:", e)
    finally:
        if inserter:
            inserter.connection.close_connection()


if __name__ == "__main__":
    main()
