# TDT4225 Porto Taxi

This project contains the Python and SQL work for Assignment 2 in TDT4225 - Very Large, Distributed Data Volumes. It uses the Porto Taxi Trajectory dataset and a MySQL 8.0.39 database.

## Project Structure

```text
.
├── compose.yaml                # MySQL 8.0.39 service definition
├── pyproject.toml              # Project dependencies and Python version
├── requirements.txt            # Assignment dependencies
├── src/
│   ├── eda.ipynb               # Exploratory data analysis and data-quality checks
│   ├── insert.py               # Cleans and inserts data into MySQL
│   ├── queries.py              # Runs the Part 2 assignment queries
│   ├── DbConnector.py          # Connects Python to the MySQL database
│   ├── data/porto.csv          # Porto Taxi Trajectory dataset
│   └── results/figures/*.png   # Analysis results
│   └── results/queries/*.csv   # Queries results
└── .env                        # Local database passwords; do not commit secrets
```

## Requirements

- Python 3.13 or newer
- `uv`
- Docker Desktop or another Docker installation

## Setup

Run these commands from the project root.

### 1. Install the Python environment

```bash
uv sync --locked
```

This creates or updates the project virtual environment from `uv.lock` and installs the required dependencies.

### 2. Configure database credentials

Create or update `.env` in the project root:

```dotenv
MYSQL_ROOT_PASSWORD=choose_a_root_password
MYSQL_PASSWORD=choose_a_database_password
```

The database user created by Docker is `porto`, the database is `porto`, and the database password is read from `MYSQL_PASSWORD`.

### 3. Start MySQL

Start the database with Docker Compose:

```bash
docker compose up -d
```

The MySQL server is available at `127.0.0.1:3306`. Compose uses the MySQL `8.0.39` image declared in `compose.yaml`; no separate `docker pull` command is required because Docker pulls the image automatically when needed.

Check that the container is running:

```bash
docker compose ps
```

To stop the database without deleting its data:

```bash
docker compose down
```

### 4. Upload Dataset

Download the Porto Taxi Trajectory dataset (`porto.csv`) from [Kaggle](https://www.kaggle.com/datasets/crailtap/taxi-trajectory) and upload it to the `src/data/` folder:

```text
src/
└── data/
    └── porto.csv
```

Make sure the file is named exactly `porto.csv`.

## Assignment Workflow

### Part 1: EDA, cleaning, and insertion

1. Open `src/eda.ipynb` to inspect the dataset and perform exploratory data analysis. The notebook examines the data distribution, trajectory contents, missing values, invalid trajectories, and other data-quality issues. Its figures and results are stored in `src/results/figures`.
2. Run `src/insert.py` to clean the dataset and insert the prepared data into the MySQL database.
3. The database schema and cleaning decisions should be documented in the report, including the relevant `CREATE TABLE` statements.

### Part 2: Database queries

Run `src/queries.py` after the data has been inserted. It handles the assignment queries, including taxi and trip counts, trip averages, the most-used call types, duration and distance statistics, City Hall proximity, invalid trips, midnight crossers, circular trips, and taxi idle time. The results are stored in `src/results/queries`.

## Running Python Files

Because the project uses `uv`, run Python commands from the project root with:

```bash
uv run python src/insert.py
uv run python src/queries.py
```

`DbConnector.py` loads `MYSQL_PASSWORD` from `.env` and connects to the MySQL database running in Docker. Start the database before running scripts that use the connector.


## Viewing the congestion heatmap

The Folium congestion heatmap should be served through a local HTTP server rather than opened directly from Finder.

From the project root, run:

```bash
cd src/results
uv run python -m http.server 8000
```

Then open the generated heatmap in your browser at:

```text
http://localhost:8000/congestion_heatmap.html
```

Keep the terminal running while viewing the map. Folium includes the required OpenStreetMap attribution. Avoid excessive reloading or large-scale requests to the public OpenStreetMap servers.

## Troubleshooting

- If the connection fails, verify that Docker is running and that `docker compose ps` shows the MySQL service as healthy or running.
- If credentials were changed after the database was first started, remove the existing Docker volume and start again. This deletes the local database data:

  ```bash
  docker compose down -v
  docker compose up -d
  ```

- Do not commit `.env` or real database passwords to the repository.
