#!/usr/bin/env bash
# Install agentop.wolfdrawer.net on the proxy host and get its certificate.
# Run from the devhost. Safe to run again.
#
# Usage: deploy/nginx/setup-wolfdrawer.sh [ssh-host]
set -euo pipefail

DOMAIN=agentop.wolfdrawer.net
REMOTE=${1:-pisces-01}
IP=157.120.33.185
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Let's Encrypt validates through public DNS, so check that first.
RESOLVED=$(dig +short @1.1.1.1 "$DOMAIN" A | tail -1)
if [[ "$RESOLVED" != "$IP" ]]; then
    echo "$DOMAIN resolves to '${RESOLVED:-nothing}', not $IP. Nothing was changed." >&2
    exit 1
fi

scp -q "$HERE/wolfdrawer.conf" "$REMOTE:/tmp/wolfdrawer.conf"

ssh "$REMOTE" DOMAIN="$DOMAIN" 'bash -s' <<'REMOTE'
set -euo pipefail
AVAILABLE=/etc/nginx/sites-available/wolfdrawer
ENABLED=/etc/nginx/sites-enabled/wolfdrawer

apply() {
    sudo cp "$1" "$AVAILABLE"
    sudo ln -sf "$AVAILABLE" "$ENABLED"
    if ! sudo nginx -t; then
        sudo rm -f "$ENABLED"
        echo "nginx refused the config, so the site was removed." >&2
        exit 1
    fi
    sudo systemctl reload nginx
}

sudo mkdir -p /var/www/wolfdrawer-acme
sed '/^# --- https/,$d' /tmp/wolfdrawer.conf > /tmp/wolfdrawer-http.conf
if ! sudo test -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem"; then
    apply /tmp/wolfdrawer-http.conf
    sudo certbot certonly --webroot -w /var/www/wolfdrawer-acme -d "$DOMAIN" \
        --non-interactive --agree-tos --register-unsafely-without-email \
        --deploy-hook "systemctl reload nginx"
fi
apply /tmp/wolfdrawer.conf
rm -f /tmp/wolfdrawer.conf /tmp/wolfdrawer-http.conf
sudo certbot renew --dry-run --quiet && echo "renewal dry run passed"
# Certificates last 90 days, and this timer is what renews them.
sudo systemctl enable --now certbot.timer
REMOTE

echo "Done: https://$DOMAIN"
