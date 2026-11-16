#!/usr/bin/env bash
# Rebuild docs/Brightleaf-Support-Agent-Overview.pdf from docs/agent-overview.html
cd "$(dirname "$0")"
CHROME=$(find ~/.cache/ms-playwright -type f -name chrome-headless-shell | head -1)
"$CHROME" --no-sandbox --no-pdf-header-footer \
  --print-to-pdf=docs/Brightleaf-Support-Agent-Overview.pdf "file://$PWD/docs/agent-overview.html"
