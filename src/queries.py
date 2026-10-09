"""Run the Porto taxi queries against the Porto taxi database and print the results."""

from decimal import Decimal
from math import cos, radians
from pathlib import Path

import csv

from tabulate import tabulate

from DbConnector import DbConnector

CITY_HALL = (-8.62911, 41.15794)    # Porto City Hall: (longitude, latitude)
CITY_HALL_RADIUS_M = 100
CIRCULAR_TRIP_RADIUS_M = 50
MIN_VALID_GPS_POINTS = 3
METERS_PER_DEGREE_LAT = 111_320
PREVIEW_ROWS = 20
RESULTS_DIR = Path(__file__).resolve().parent / "results" / "queries"


class QueryProgram:
    """Run the Part 2 assignment queries and print each result as a table."""

    def __init__(self):
        """Connect to the database and keep the connection and cursor."""
        self.connection = DbConnector()
        self.db_connection = self.connection.db_connection
        self.cursor = self.connection.cursor

    def fetch(self, query, params=None) -> tuple[list[tuple], list[str]]:
        """Execute a query and return its rows and column names.

        Args:
            query (str): The SQL query to execute.
            params (tuple | dict | None): Parameters substituted into the query.
        Returns:
            tuple: The fetched rows and the list of column names.
        """
        self.cursor.execute(query, params)
        rows = self.cursor.fetchall()
        return rows, list(self.cursor.column_names)

    @staticmethod
    def print_header(title):
        """Print a task title between two separator lines.

        Args:
            title (str): The title to print.
        Returns:
            None
        """
        print(f"\n{'=' * 80}\n{title}\n{'=' * 80}")

    @staticmethod
    def print_table(rows, headers, limit=None):
        """Print rows as a table, optionally showing only the first `limit` rows.

        Args:
            rows (list[tuple]): The rows to print.
            headers (list[str]): The column names.
            limit (int | None): The maximum number of rows to show, or None for all.
        Returns:
            None
        """
        shown = rows if limit is None else rows[:limit]
        print(tabulate(shown, headers=headers, tablefmt="psql", floatfmt=".2f"))
        if limit is not None and len(rows) > limit:
            print(f"... showing {limit} of {len(rows):,} rows")

    @staticmethod
    def save_csv(name, rows, headers):
        """Save all rows of a query result as a CSV file in the results/queries folder.

        Decimal and float values are rounded to 4 decimals, and datetimes are
        written as YYYY-MM-DD HH:MM:SS.

        Args:
            name (str): The file name without extension, e.g. "task_3".
            rows (list[tuple]): The rows to save.
            headers (list[str]): The column names, written as the first row.
        Returns:
            None
        """
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = RESULTS_DIR / f"{name}.csv"
        with open(path, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            for row in rows:
                writer.writerow(
                    round(float(value), 4) if isinstance(value, (Decimal, float)) else value
                    for value in row
                )
        print(f"Saved {len(rows):,} rows to {path.relative_to(RESULTS_DIR.parents[1])}")

    def task_1(self):
        """Count the number of taxis, trips, and total GPS points.

        Returns:
            None
        """
        self.print_header("1. Number of taxis, trips, and total GPS points")
        rows, _ = self.fetch("SELECT COUNT(DISTINCT taxi_id), COUNT(*) FROM trips")
        taxis, trips = rows[0]
        rows, _ = self.fetch("SELECT COUNT(*) FROM gps_points")
        gps_points = rows[0][0]
        self.print_table([(f"{taxis:,}", f"{trips:,}", f"{gps_points:,}")], ["Taxis", "Trips", "GPS points"])
        self.save_csv("task_1", [(taxis, trips, gps_points)], ["taxis", "trips", "gps_points"])

    def task_2(self):
        """Compute the average number of trips per taxi.

        Returns:
            None
        """
        self.print_header("2. Average number of trips per taxi")
        rows, headers = self.fetch("""
            SELECT COUNT(*) / COUNT(DISTINCT taxi_id) AS avg_trips_per_taxi
            FROM trips
        """)
        self.print_table(rows, headers)
        self.save_csv("task_2", rows, headers)

    def task_3(self):
        """List the top 20 taxis with the most trips.

        Returns:
            None
        """
        self.print_header("3. Top 20 taxis with the most trips")
        rows, headers = self.fetch("""
            SELECT taxi_id, COUNT(*) AS trips
            FROM trips
            GROUP BY taxi_id
            ORDER BY trips DESC
            LIMIT 20
        """)
        self.print_table(rows, headers)
        self.save_csv("task_3", rows, headers)

    def task_4a(self):
        """Find the most used call type per taxi.

        Ties are broken alphabetically by call type. Prints the first rows, a "..."
        row and the last taxi, and saves the full table as CSV.

        Returns:
            None
        """
        self.print_header("4a. Most used call type per taxi")
        rows, headers = self.fetch("""
            WITH call_type_counts AS (
                SELECT taxi_id, call_type, COUNT(*) AS trips
                FROM trips
                GROUP BY taxi_id, call_type
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY taxi_id ORDER BY trips DESC, call_type
                       ) AS call_type_rank
                FROM call_type_counts
            )
            SELECT taxi_id AS TAXI_ID,
                   call_type AS CALL_TYPE,
                   trips AS max_trips
            FROM ranked
            WHERE call_type_rank = 1
            ORDER BY taxi_id
        """)

        shown = rows
        if len(rows) > PREVIEW_ROWS + 1:
            shown = rows[:PREVIEW_ROWS] + [("...", "", "")] + rows[-1:]
        self.print_table(shown, headers)
        print(f"{len(rows):,} taxis in total")
        self.save_csv("task_4a", rows, headers)

    def task_4b(self, exclude_bad_measure=False):
        """Compute the average duration and distance per call type, and the share
        of trips starting in the time bands 00-06, 06-12, 12-18, and 18-24.

        Args:
            exclude_bad_measure (bool): Exclude trips flagged as BAD_MEASURE.
        Returns:
            None
        """
        label = "excluding BAD_MEASURE trips" if exclude_bad_measure else "all trips"
        trip_filter = "WHERE BAD_MEASURE = FALSE" if exclude_bad_measure else ""
        self.print_header(f"4b. Average duration, distance, and start time bands per call type ({label})")
        rows, headers = self.fetch(f"""
            SELECT call_type,
                   COUNT(*) AS trips,
                   AVG(duration_s) / 60 AS avg_duration_min,
                   AVG(distance_km) AS avg_distance_km,
                   100 * AVG(HOUR(start_time) < 6) AS `00-06 %`,
                   100 * AVG(HOUR(start_time) >= 6 AND HOUR(start_time) < 12) AS `06-12 %`,
                   100 * AVG(HOUR(start_time) >= 12 AND HOUR(start_time) < 18) AS `12-18 %`,
                   100 * AVG(HOUR(start_time) >= 18) AS `18-24 %`
            FROM trips
            {trip_filter}
            GROUP BY call_type
            ORDER BY call_type
        """)
        self.print_table(rows, headers)
        name = "task_4b_without_bad_measure" if exclude_bad_measure else "task_4b"
        self.save_csv(name, rows, headers)

    def task_5(self, exclude_bad_measure=False):
        """List the top 20 taxis by total hours driven, with total distance driven.

        Args:
            exclude_bad_measure (bool): Exclude trips flagged as BAD_MEASURE.
        Returns:
            None
        """
        label = "excluding BAD_MEASURE trips" if exclude_bad_measure else "all trips"
        trip_filter = "WHERE BAD_MEASURE = FALSE" if exclude_bad_measure else ""
        self.print_header(f"5. Taxis with the most total hours and distance driven ({label})")
        rows, headers = self.fetch(f"""
            SELECT taxi_id,
                   SUM(duration_s) / 3600 AS total_hours,
                   SUM(distance_km) AS total_distance_km
            FROM trips
            {trip_filter}
            GROUP BY taxi_id
            ORDER BY total_hours DESC
            LIMIT 20
        """)
        self.print_table(rows, headers)
        name = "task_5_without_bad_measure" if exclude_bad_measure else "task_5"
        self.save_csv(name, rows, headers)

    def task_6(self):
        """Find the trips with a GPS point within 100 m of Porto City Hall.

        A bounding box around City Hall filters the GPS points before the exact
        spherical distance is computed with ST_Distance_Sphere.

        Returns:
            None
        """
        self.print_header(f"6. Trips passing within {CITY_HALL_RADIUS_M} m of Porto City Hall")
        lon, lat = CITY_HALL
        # Bounding box prefilter so the exact distance is only computed for nearby points
        delta_lat = CITY_HALL_RADIUS_M / METERS_PER_DEGREE_LAT
        delta_lon = delta_lat / cos(radians(lat))
        rows, headers = self.fetch("""
            SELECT t.trip_id, t.taxi_id, t.start_time, near.closest_distance_m
            FROM (
                SELECT trip_id,
                       MIN(ST_Distance_Sphere(
                           POINT(longitude, latitude), POINT(%(lon)s, %(lat)s)
                       )) AS closest_distance_m
                FROM gps_points
                WHERE latitude BETWEEN %(min_lat)s AND %(max_lat)s
                  AND longitude BETWEEN %(min_lon)s AND %(max_lon)s
                GROUP BY trip_id
                HAVING closest_distance_m <= %(radius)s
            ) AS near
            JOIN trips t ON t.trip_id = near.trip_id
            ORDER BY t.start_time
        """, {
            "lon": lon,
            "lat": lat,
            "min_lat": lat - delta_lat,
            "max_lat": lat + delta_lat,
            "min_lon": lon - delta_lon,
            "max_lon": lon + delta_lon,
            "radius": CITY_HALL_RADIUS_M,
        })
        print(f"{len(rows):,} trips passed within {CITY_HALL_RADIUS_M} m of Porto City Hall")
        self.print_table(rows, headers, limit=PREVIEW_ROWS)
        self.save_csv("task_6", rows, headers)

    def task_7(self):
        """Count the invalid trips, i.e. trips with fewer than 3 GPS points.

        Returns:
            None
        """
        self.print_header(f"7. Invalid trips (fewer than {MIN_VALID_GPS_POINTS} GPS points)")
        rows, _ = self.fetch("""
            SELECT COUNT(*) AS invalid_trips,
                   (SELECT COUNT(*) FROM trips) AS total_trips
            FROM (
                SELECT t.trip_id
                FROM trips t
                LEFT JOIN gps_points g ON g.trip_id = t.trip_id
                GROUP BY t.trip_id
                HAVING COUNT(g.trip_id) < %s
            ) AS invalid
        """, (MIN_VALID_GPS_POINTS,))
        invalid, total = rows[0]
        rows = [(invalid, total, 100 * invalid / total)]
        headers = ["invalid_trips", "total_trips", "share_pct"]
        self.print_table(rows, headers)
        self.save_csv("task_7", rows, headers)

    def task_8(self):
        """Find the trips that start on one calendar day and end on the next.

        The end time is the start time plus the trip duration.

        Returns:
            None
        """
        self.print_header("8. Trips that started on one day and ended on the next (midnight crossers)")
        rows, headers = self.fetch("""
            SELECT trip_id, taxi_id, start_time, end_time, duration_s / 60 AS duration_min
            FROM (
                SELECT trip_id, taxi_id, start_time, duration_s,
                       DATE_ADD(start_time, INTERVAL duration_s SECOND) AS end_time
                FROM trips
            ) AS trip_times
            WHERE DATEDIFF(end_time, start_time) = 1
            ORDER BY start_time
        """)
        print(f"{len(rows):,} trips crossed midnight")
        self.print_table(rows, headers, limit=PREVIEW_ROWS)
        self.save_csv("task_8", rows, headers)

    def task_9(self):
        """Find the circular trips, whose first and last GPS points are within 50 m.

        Trips with fewer than 3 GPS points are excluded.

        Returns:
            None
        """
        self.print_header(f"9. Circular trips (start and end within {CIRCULAR_TRIP_RADIUS_M} m)")
        # Trips with fewer than 3 GPS points are invalid (task 7) and are excluded,
        # since they trivially start and end at almost the same place
        rows, headers = self.fetch("""
            SELECT t.trip_id, t.taxi_id, t.start_time, t.distance_km,
                   ST_Distance_Sphere(
                       POINT(s.longitude, s.latitude), POINT(e.longitude, e.latitude)
                   ) AS start_end_distance_m
            FROM (
                SELECT trip_id, MAX(point_order) AS last_order
                FROM gps_points
                GROUP BY trip_id
                HAVING COUNT(*) >= %(min_points)s
            ) AS last_point
            JOIN gps_points s
              ON s.trip_id = last_point.trip_id AND s.point_order = 0
            JOIN gps_points e
              ON e.trip_id = last_point.trip_id AND e.point_order = last_point.last_order
            JOIN trips t ON t.trip_id = last_point.trip_id
            WHERE ST_Distance_Sphere(
                POINT(s.longitude, s.latitude), POINT(e.longitude, e.latitude)
            ) <= %(radius)s
            ORDER BY t.start_time
        """, {"min_points": MIN_VALID_GPS_POINTS, "radius": CIRCULAR_TRIP_RADIUS_M})
        print(f"{len(rows):,} circular trips found")
        self.print_table(rows, headers, limit=PREVIEW_ROWS)
        self.save_csv("task_9", rows, headers)

    def task_10(self):
        """List the top 20 taxis with the highest average idle time between consecutive trips.

        Idle time is the next trip's start time minus the current trip's end time.

        Returns:
            None
        """
        self.print_header("10. Top 20 taxis with the highest average idle time between trips")
        # Overlapping trips give a negative gap, which is counted as zero idle time
        rows, headers = self.fetch("""
            WITH ordered_trips AS (
                SELECT taxi_id,
                       DATE_ADD(start_time, INTERVAL duration_s SECOND) AS end_time,
                       LEAD(start_time) OVER (
                           PARTITION BY taxi_id ORDER BY start_time
                       ) AS next_start_time
                FROM trips
            )
            SELECT taxi_id,
                   AVG(GREATEST(TIMESTAMPDIFF(SECOND, end_time, next_start_time), 0)) / 3600
                       AS avg_idle_hours,
                   COUNT(*) AS idle_periods
            FROM ordered_trips
            WHERE next_start_time IS NOT NULL
            GROUP BY taxi_id
            ORDER BY avg_idle_hours DESC
            LIMIT 20
        """)
        self.print_table(rows, headers)
        self.save_csv("task_10", rows, headers)


def main():
    """Connect to the database, run every task in order, and close the connection.

    Returns:
        None
    """
    program = None
    try:
        program = QueryProgram()
        program.task_1()
        program.task_2()
        program.task_3()
        program.task_4a()
        program.task_4b()
        program.task_4b(exclude_bad_measure=True)
        program.task_5()
        program.task_5(exclude_bad_measure=True)
        program.task_6()
        program.task_7()
        program.task_8()
        program.task_9()
        program.task_10()
    except Exception as e:
        print("ERROR: Failed to use database:", e)
    finally:
        if program:
            program.connection.close_connection()


if __name__ == '__main__':
    main()
