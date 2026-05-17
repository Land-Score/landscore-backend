#!/bin/bash
set -e

# Try to apply pending migrations.
# If upgrade fails (e.g. tables already created by create_all() on a
# previous start), stamp the version table to the current head so that
# Alembic knows migration 0001 is done and won't retry it next time.
alembic upgrade head || alembic stamp head

exec python -m app.main
