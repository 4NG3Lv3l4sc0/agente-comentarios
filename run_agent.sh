#!/usr/bin/env bash

set -e

cd ~/genshin-agent

echo "=========================================="
echo "GENSHIN GIVEAWAY AGENT"
echo "=========================================="

docker compose up -d

echo
echo "Esperando servicios..."
sleep 3

echo
echo "Lanzando flujo completo..."
echo

docker exec genshin_agent python -u /app/app/agent.py
