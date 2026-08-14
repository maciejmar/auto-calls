#!/usr/bin/env bash
# Run on the production server (via SSH from GitHub Actions) inside the
# repo's working directory. Pulls the latest commit, rebuilds the app image,
# restarts the stack, then applies any pending Alembic migrations.
set -euo pipefail

git pull --ff-only origin master

docker compose -f docker-compose.prod.yml up -d --build

docker compose -f docker-compose.prod.yml exec -T app alembic upgrade head

docker compose -f docker-compose.prod.yml ps
