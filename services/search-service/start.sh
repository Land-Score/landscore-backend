#!/bin/bash
set -e

# Try to apply pending migrations.
# If upgrade fails (e.g. tables already created by create_all() on a
# previous startup before alembic_version_search was stamped),
# stamp the version table so Alembic knows 0001 is done and won't retry.
alembic upgrade head || alembic stamp head

exec python -m app.main
