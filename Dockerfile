FROM caddy:2.11.4-alpine

COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY Caddyfile /etc/caddy/Caddyfile
COPY Caddyfile.maintenance /etc/caddy/Caddyfile.maintenance
ENTRYPOINT ["/entrypoint.sh"]
