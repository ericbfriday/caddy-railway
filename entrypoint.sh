#!/bin/sh
set -eu

missing=
for name in HERMES_HOST COMFY_HOST HERMES_TAILSCALE_HOST COMFY_TAILSCALE_HOST TAILSCALE_PROXY_URL AUTHELIA_UPSTREAM; do
    eval "value=\${$name-}"
    if [ -z "$value" ]; then
        missing="$missing $name"
    fi
done

if [ -n "$missing" ]; then
    echo "Setup pending; missing environment variables:$missing" >&2
    exec caddy run --config /etc/caddy/Caddyfile.maintenance --adapter caddyfile
fi

if [ "$HERMES_HOST" = "$COMFY_HOST" ]; then
    echo "Setup pending; HERMES_HOST and COMFY_HOST must differ" >&2
    exec caddy run --config /etc/caddy/Caddyfile.maintenance --adapter caddyfile
fi

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
