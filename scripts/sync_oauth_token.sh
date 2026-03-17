#!/usr/bin/env bash
# Sync Claude Max OAuth token from ~/.claude/.credentials.json into .env
# Usage: source scripts/sync_oauth_token.sh
#   or:  ./scripts/sync_oauth_token.sh  (updates .env in-place)

set -euo pipefail

CRED_FILE="${HOME}/.claude/.credentials.json"
ENV_FILE="${1:-.env}"

if [ ! -f "$CRED_FILE" ]; then
    echo "Error: $CRED_FILE not found. Run 'claude setup-token' first." >&2
    exit 1
fi

TOKEN=$(python3 -c "
import json, sys, time
cred = json.load(open('$CRED_FILE'))
oauth = cred.get('claudeAiOauth', {})
token = oauth.get('accessToken', '')
expires = oauth.get('expiresAt', 0)
if not token:
    print('Error: no accessToken found', file=sys.stderr)
    sys.exit(1)
remaining = (expires / 1000) - time.time()
if remaining < 0:
    print(f'Warning: token expired {-remaining:.0f}s ago. Run \"claude setup-token\" to refresh.', file=sys.stderr)
elif remaining < 3600:
    print(f'Warning: token expires in {remaining:.0f}s ({remaining/60:.0f}min).', file=sys.stderr)
print(token)
")

if [ -z "$TOKEN" ]; then
    exit 1
fi

# Update or append ANTHROPIC_API_KEY in .env
if grep -q '^ANTHROPIC_API_KEY=' "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${TOKEN}|" "$ENV_FILE"
    echo "Updated ANTHROPIC_API_KEY in $ENV_FILE"
else
    echo -e "\n# === Anthropic (Claude Max OAuth — auto-synced) ===" >> "$ENV_FILE"
    echo "ANTHROPIC_API_KEY=${TOKEN}" >> "$ENV_FILE"
    echo "Appended ANTHROPIC_API_KEY to $ENV_FILE"
fi
