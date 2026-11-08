#!/usr/bin/env bash
# Rebuild test workflows, (re)import into a throwaway n8n, publish, start n8n on 127.0.0.1:5688.
# Usage: tests/e2e/run_n8n.sh <scratch-dir>   (needs n8n installed in <scratch-dir>/n8n-e2e)
set -e
SP=${1:?scratch dir}; ROOT=$(cd "$(dirname "$0")/../.." && pwd)
N8N=$SP/n8n-e2e/node_modules/.bin/n8n
export N8N_USER_FOLDER=$SP/e2e/home N8N_DIAGNOSTICS_ENABLED=false N8N_PORT=5688 N8N_LISTEN_ADDRESS=127.0.0.1 N8N_SECURE_COOKIE=false
pkill -f "n8n start" 2>/dev/null || true; sleep 2
python3 "$ROOT/tools/build_workflows.py" --config "$SP/e2e/config.test.json" --test --mock http://127.0.0.1:9099 --out "$SP/e2e/wf" >/dev/null
python3 "$ROOT/tests/validate_workflows.py" "$SP/e2e/wf" | tail -1
$N8N import:workflow --separate --input="$SP/e2e/wf" >/dev/null 2>&1
for f in "$SP"/e2e/wf/*.json; do $N8N publish:workflow --id="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['id'])" "$f")" >/dev/null 2>&1; done
(nohup $N8N start > "$SP/e2e/n8n.log" 2>&1 &)
for i in $(seq 1 60); do sleep 2; grep -q "Activated workflow \"06" "$SP/e2e/n8n.log" && break; done
sleep 3; grep -c "Activated workflow" "$SP/e2e/n8n.log"
