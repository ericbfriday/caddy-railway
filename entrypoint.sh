#!/bin/sh
set -eu

for name in HERMES_HOST COMFY_HOST HERMES_TAILSCALE_HOST COMFY_TAILSCALE_HOST TAILSCALE_PROXY_URL AUTHELIA_UPSTREAM; do
    eval "value=\${$name-}"
    if [ -z "$value" ]; then
        echo "Missing required environment variable: $name" >&2
        exit 1
    fi
done

if [ "$HERMES_HOST" = "$COMFY_HOST" ]; then
    echo "HERMES_HOST and COMFY_HOST must differ" >&2
    exit 1
fi

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
