#!/usr/bin/env bash
# Re-provisiona el dashboard RED en Grafana via API (respaldo del
# provisioning por archivo). Uso:
#   GRAFANA_PASSWORD='Cristina2019.' ./scripts/provision_dashboard.sh
# Variables opcionales: GRAFANA_URL (defecto http://localhost:3029),
#                       GRAFANA_USER (defecto admin)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASH_JSON="${SCRIPT_DIR}/../dashboards/red-dashboard.json"

GRAFANA_URL="${GRAFANA_URL:-http://localhost:3029}"
GRAFANA_USER="${GRAFANA_USER:-admin}"

if [[ -z "${GRAFANA_PASSWORD:-}" ]]; then
  echo "ERROR: define la contraseña de Grafana, ej.:"
  echo "  GRAFANA_PASSWORD='tu-clave' $0"
  exit 1
fi

if [[ ! -f "$DASH_JSON" ]]; then
  echo "ERROR: no encuentro $DASH_JSON"
  exit 1
fi

python3 - "$DASH_JSON" > /tmp/dash_payload.json <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
print(json.dumps({"dashboard": d, "folderUid": "", "overwrite": True,
                  "message": "Re-provision via script"}))
EOF

http_code=$(curl -s -o /tmp/dash_response.json -w '%{http_code}' \
  -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
  -H 'Content-Type: application/json' \
  -X POST "${GRAFANA_URL}/api/dashboards/db" \
  -d @/tmp/dash_payload.json)

if [[ "$http_code" == "200" ]]; then
  echo "OK: dashboard provisionado en ${GRAFANA_URL}"
  grep -o '"url":"[^"]*"' /tmp/dash_response.json
else
  echo "ERROR: HTTP $http_code"
  cat /tmp/dash_response.json
  exit 1
fi
