# mainboard_assembly1

## Configuration

In PowerShell, create `.env` in the project root by running
`Copy-Item .env.example .env`. Set a unique `SECRET_KEY`, a strong
`ADMIN_PASSWORD`, and the database connection values; do not leave the example
placeholders or required database values blank. The app loads this file
automatically when started with Python or Docker Compose and exits during
startup if any required value is missing.

For local development, install dependencies with `pip install -r requirements.txt`
and start with `python run.py`. To run in Docker, use `docker compose up --build`.
The container uses Gunicorn and stores timestamps in the `Asia/Manila` timezone.
Gunicorn runs two workers, and MySQL connections use five-second connect, read,
and write timeouts by default. Scan write transactions allow up to 15 seconds
for the database response, and admin reads allow up to 20 seconds. If a database
operation times out, API requests return HTTP 503 with a retry message rather
than waiting for the Gunicorn worker timeout. Production-sized admin query
performance still needs validation against the deployment database.
This app reads and writes the production `dentalmonitoring.ledboard_main` and
`dentalmonitoring.mainboard_assembly1` tables.

Scan the LED-board serial first. It must exist in `ledboard_main` and have
`test`, `soldering`, and `vi2` set to `1`.
Then scan the mainboard serial. It must exist in `mainboard_main` and have
`progtest`, `assembly1`, `assembly5`, `test`, `lasermarking`, and `packaging` set to `1`
before the app records the assembly1 result in `mainboard_assembly1`.
Each assembly1 record includes the scanned LED-board serial in its `ledboard`
column. Failed inspections are recorded with mainboard serial suffixes `_1`
through `_3`; a fourth FAIL attempt is not accepted.