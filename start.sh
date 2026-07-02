#!/usr/bin/env bash
cd "$(dirname "$0")"
docker compose -f docker-compose.yml -f docker-compose.linux.yml up -d --build
