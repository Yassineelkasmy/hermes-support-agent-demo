#!/usr/bin/env bash
# Clear tickets created in earlier takes so the recording starts at T-4101.
cd "$(dirname "$0")" && echo '[]' > data/tickets.json && echo "Tickets cleared."
