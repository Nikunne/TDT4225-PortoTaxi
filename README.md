## SETUP

# Set up a uv python venv, run in root
>uv sync --locked

Everything should be fine and set up after that.

# Folder structure
code in /src/

the rest in root



# Docker

Docker is installed through
> docker pull mysql:latest

 Run
>docker compose up -d

## Import the taxi data

Put `porto.csv` in `data/`. The importer uses `src/DbConnector.py` and its
`MYSQL_PASSWORD` environment variable (also loaded from `.env`).

Check a small part of the CSV without connecting to MySQL:

```sh
uv run python src/DbInserter.py --dry-run --limit 10000
```

Import the full file after starting MySQL:

```sh
uv run python src/DbInserter.py
```

Use `--csv /path/to/porto.csv`, `--batch-size 500`, or `--limit 10000` to change
the input, transaction size, or number of source records read. The default input
path works regardless of the current working directory.

The importer creates `trips` for metadata and calculated fields, and
`trajectories` for routes as MySQL LINESTRING geometries (SRID 4326). The
`CREATE TABLE` statements are in `src/DbInserter.py`. GPS coordinates are
explicitly interpreted as longitude, latitude, following the
[MySQL axis-order option](https://dev.mysql.com/doc/refman/8.0/en/gis-wkt-functions.html).
It does not migrate pre-existing tables with a different schema.

Cleaning and storage choices:

- Keep the first row for each trip ID. Existing database trips are skipped on
  subsequent runs; they are not updated. Run only one importer at a time.
- Keep long trips, including IQR outliers. No thresholds from the EDA sample
  are used to discard data.
- Preserve the original `MISSING_DATA` as `missing_data`. A separate
  `route_status` records `ok`, `empty`, `single_point`, `invalid_json`, or
  `invalid_coordinates`. No route geometry is created for unusable routes.
- Retain short trips so the assignment's invalid-trip count can use
  `num_points < 3`. A two-point route is a usable LINESTRING but is still
  an invalid trip under that assignment definition.
- Sum consecutive Haversine distances in metres. Duration is
  `(num_points - 1) * 15` seconds. Empty or malformed routes have NULL
  distance, duration, and end time; a single point has zero distance/duration.
  For trips marked missing, these describe the recorded points and may
  underestimate the complete trip.
- Store start/end times as UTC DATETIME values. Convert to Europe/Lisbon when
  answering questions about local hours or calendar days.
- Preserve source day types and origin IDs, mapping blank origin IDs to SQL
  NULL. No holiday reclassification is performed.

CSV rows are streamed in batches; only trip IDs are retained for deduplication.
Both tables are committed together for each batch. A failed database batch is
rolled back; earlier batches remain committed and rerunning skips them.
Malformed metadata stops the import with a record number. Invalid route data
is retained as trip metadata with a route status and NULL geometry. Dry-run
counts describe preprocessing, not MySQL compatibility or existing database rows.

Run the focused tests with:

```sh
uv run python -m unittest discover -s tests -v
```
