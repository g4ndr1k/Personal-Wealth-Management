#!/bin/bash
echo "=== Post-Reboot System Check ==="
echo "Waiting 60 seconds for services to start..."
sleep 60

echo ""
echo "=== Ollama ==="
curl -sf http://127.0.0.1:11434/api/tags > /dev/null && echo "✅ Running" || echo "❌ Not running"

echo ""
echo "=== Bridge ==="
TOKEN=$(cat ~/agentic-ai/secrets/bridge.token)
curl -sf -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9100/health > /dev/null && echo "✅ Running" || echo "❌ Not running"

echo ""
echo "=== Docker Agent ==="
cd ~/agentic-ai
docker compose ps 2>/dev/null || echo "❌ Docker not ready yet"

echo ""
echo "=== Docker->Ollama ==="
docker run --rm --add-host=host.docker.internal:host-gateway curlimages/curl:latest curl -sf http://host.docker.internal:11434/api/tags > /dev/null 2>&1 && echo "✅ Connected" || echo "❌ Not connected"
