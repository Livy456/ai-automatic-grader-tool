#!/bin/sh
# docker-entrypoint.sh
#
# Runs Alembic migrations to HEAD before starting the Flask server.
# This ensures the database schema is always up to date on container start,
# including creating the `users` table (and all other core tables) on a
# fresh database.
#
# Usage: set as ENTRYPOINT in Dockerfile (see Dockerfile).
 
set -e
 
echo "[entrypoint] Running database migrations..."
alembic upgrade head
echo "[entrypoint] Migrations complete. Starting application..."
 
exec "$@"