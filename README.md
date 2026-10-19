# Railway edge for Hermes Agent

This fork adapts the [official Caddy Railway template](https://caddyserver.com/docs/quick-starts/railway) for one public, authenticated Hermes app and its Authelia portal. Railway terminates browser HTTPS. Caddy checks each Hermes request with Authelia, then reaches the Windows workstation through a private Tailscale connection. The home router does not need a port forward. ComfyUI has no public route; existing private `svc:comfyui` and home-network access are not removed by this repository.

```text
Browser -> Railway TLS -> this Caddy service -> Authelia (Railway private network)
                                            -> Tailscale proxy (Railway private network)
                                            -> private node Serve HTTPS port -> Hermes
```

## What this repository provides

- Routes `AUTH_HOST` to the private Authelia service and `HERMES_HOST` to the Windows node's verified private Tailscale Serve endpoint. The backhaul defaults to HTTPS port 443 and supports optional `HERMES_TAILSCALE_PORT`.
- Requires Authelia `forward_auth` before every app request. If Authelia fails, the app proxy does not run.
- Returns 404 for the former ComfyUI hostname, Railway-generated URL, and unknown hosts. `/healthz` skips authentication on every host; the Authelia portal is intentionally reachable without a session. Legacy `COMFY_HOST` / `COMFY_TAILSCALE_HOST` variables cannot enable a route and can be removed.
- Preserves Railway's `X-Real-IP` as `X-Forwarded-For` for both the portal and forward-auth requests so Authelia's OTP client-address binding remains stable. This assumes Railway supplies that header; do not expose the container directly to untrusted clients or change that trust boundary without review.
- Uses Caddy's built-in `encode zstd gzip`, HTTP streaming, WebSocket proxying, and HTTP network proxy transport. No Caddy plugin is needed. The experimental Tailscale Caddy plugin crashed during configuration validation, so this deployment uses the [official Tailscale container](https://tailscale.com/docs/features/containers/docker/docker-params) in userspace mode as a separate private Railway service.

The previous VPS plan's public DNS, TLS, and edge host steps are replaced by Railway. Its private Windows Serve routes and MFA requirements still apply.

## Prerequisites

1. A Railway project with this Caddy service and a separate Authelia service. Use persistent Authelia storage and configure a user, TOTP or WebAuthn, recovery codes, and session cookies for your chosen domain. Do not place Authelia secrets or user database in this repository. See the [Authelia Caddy integration](https://www.authelia.com/integration/proxies/caddy/).
2. Tailscale MagicDNS and HTTPS enabled for the Windows node. Give the private Railway Tailscale service a restricted enrollment credential that can register `tag:railway-edge`; grant that tag access **only** to the exact Hermes node and its selected Serve port. Keep credentials in Railway variables, never in Git. See [Tailscale grants](https://tailscale.com/docs/features/access-control/grants).
3. An authenticated Hermes backend answering behind that node's private Serve route. The existing deployed path uses node Serve TCP443 to the authenticated local Hermes backend on port 9119. Do not target an unauthenticated Desktop-owned backend. Do not point public DNS at a Tailscale `100.x` address. A `svc:hermes` alias is not required.

## Deploy the Caddy service

Connect this fork's branch or merged `main` to the existing Railway Caddy service in **Settings → Source**. If it is still tied to the template, use Railway's **Eject** action as described in the [Caddy Railway guide](https://caddyserver.com/docs/quick-starts/railway), then point the service at this fork. Confirm the next build log uses `FROM caddy:2.11.4-alpine` and does not run `build.sh` or `xcaddy`; those identify the original template source. Set the service's public target port to `8080`, or set `PORT` and the target port to the same value. Remove the template's `CADDY_PLUGINS` variable; this image uses stock Caddy 2.11.4. The repository's `railway.json` explicitly selects its root Dockerfile.

Set these Railway variables on the Caddy service:

| Variable | Example | Purpose |
| --- | --- | --- |
| `AUTH_HOST` | `auth.loon.day` | Public Authelia portal hostname, without scheme |
| `HERMES_HOST` | `hermes.loon.day` | Public Hermes hostname, without scheme |
| `HERMES_TAILSCALE_HOST` | `desktop-friday.<tailnet>.ts.net` | Private Windows node hostname, without scheme or port |
| `HERMES_TAILSCALE_PORT` | `443` | Optional HTTPS Serve port for Hermes; defaults to `443` |
| `TAILSCALE_PROXY_URL` | `http://tailscale.railway.internal:1055` | Internal URL of the Tailscale service's outbound HTTP proxy |
| `AUTHELIA_UPSTREAM` | `authelia.railway.internal:9091` | Authelia service's private Railway address and port |
| `PORT` | `8080` | Optional; must match Railway's target port |

The five required variables are `AUTH_HOST`, `HERMES_HOST`, `HERMES_TAILSCALE_HOST`, `TAILSCALE_PROXY_URL`, and `AUTHELIA_UPSTREAM`. If any is missing/empty or `AUTH_HOST` equals `HERMES_HOST`, the container starts in setup mode: `/healthz` returns 200 and every other path returns 503. The startup log lists missing variable names but never their values. Once configured and redeployed, Caddy enables the portal and protected Hermes route. A healthy `/healthz` confirms only that Caddy is listening; it does **not** prove Authelia, Tailscale enrollment, or the Windows workstation is reachable. Match `PORT` to the actual Railway target port; existing deployment uses `443`, while the image default and examples use `8080`.

Configure the separate Authelia service's public portal URL as `https://auth.loon.day` and its session cookie domain for `loon.day`. Require a second factor for `hermes.loon.day`. Keep its internal port private to Railway; Caddy publishes the portal on `AUTH_HOST`.

## Deploy the private Tailscale proxy service

Add another Railway service in the **same project and environment**, using the official `tailscale/tailscale:v1.102.2` Docker image. Do not add a public domain or TCP proxy. Name it `tailscale` so its internal DNS name is `tailscale.railway.internal`, or update `TAILSCALE_PROXY_URL` accordingly. Set these variables on that service:

| Variable | Value | Purpose |
| --- | --- | --- |
| `TS_AUTHKEY` | Railway secret | Ephemeral auth key or OAuth client secret |
| `TS_EXTRA_ARGS` | `--advertise-tags=tag:railway-edge` | Stable identity for tailnet grants |
| `TS_ACCEPT_DNS` | `true` | Accept tailnet DNS settings for the node hostname |
| `TS_USERSPACE` | `true` | No TUN device or privileged container needed |
| `TS_OUTBOUND_HTTP_PROXY_LISTEN` | `:1055` | Caddy connects through Railway private networking |
| `TS_HOSTNAME` | `railway-edge` | Recognizable tailnet node name |

Use an **ephemeral** credential because Railway's container filesystem is not persistent. The official image supports this userspace HTTP proxy mode. If you later attach a Railway volume, you may instead persist Tailscale state and use `TS_AUTH_ONCE=true`. The proxy port must remain private to this Railway project; it has no per-request authentication of its own. [Tailscale userspace networking](https://tailscale.com/docs/concepts/userspace-networking), [Railway private networking](https://docs.railway.com/networking/domains/working-with-domains)

Merge a narrow grant into the existing tailnet policy after defining ownership of `tag:railway-edge`. Replace the documentation-only address below with the exact node's current tailnet IPv4 address (not a broad desktop tag or subnet). The deployed path requires only TCP443; match the rule to the selected Serve port if changing it. Audit all other grants/ACLs because allows are additive: no broader rule may also let this edge reach ComfyUI or other home services.

```json
{
  "src": ["tag:railway-edge"],
  "dst": ["100.64.0.10/32"],
  "ip": ["tcp:443"]
}
```

[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve), [grants syntax](https://tailscale.com/docs/reference/syntax/grants). Preserve existing private home-network and Service access for their intended clients; removing the public route is not an instruction to delete `svc:comfyui`.

## Windows private backhaul

Confirm `tailscale serve status` shows the Hermes node Serve route as **tailnet only**, not Funnel, targeting the authenticated local backend. Test `https://<HERMES_TAILSCALE_HOST>:<HERMES_TAILSCALE_PORT>` through the private userspace HTTP proxy with normal certificate verification. The verified deployed endpoint is the node Serve port, not a Service VIP. A disallowed tailnet device and an ordinary internet client must be unable to reach it directly. Separately confirm the edge cannot reach ComfyUI, while authorized home-network clients still can. Enrollment failures in the private Tailscale container must be repaired with a new restricted credential by an authorized operator; healthz alone does not detect them.

Hermes checks the HTTP Host and WebSocket Origin values. Configure `dashboard.public_url: https://hermes.loon.day` (or `HERMES_DASHBOARD_PUBLIC_URL`) and an auth provider before exposing the dashboard. The Caddyfile forwards the public Host while validating TLS against the private Tailscale hostname. After restarting Hermes, verify `/api/status` reports `auth_required: true` and an intended provider. A Desktop-owned loopback backend may be exempt from this gate even with a public URL; do not route such a backend publicly. [Hermes dashboard documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/web-dashboard.md)

## Custom domains and DNS

Attach **one** custom domain, `*.loon.day`, to the Caddy service in Railway, targeting the configured `PORT` (`443` in the existing deployment; `8080` in local examples). Do not add a public domain to Authelia. The wildcard covers `auth.loon.day` and `hermes.loon.day` with one domain slot, but does not enable routes for other names: `comfy.loon.day` returns 404 except `/healthz`. In Vercel DNS, use the exact wildcard CNAME, `_acme-challenge` CNAME, and ownership-verification TXT records Railway displays. This is DNS configuration, not an instruction to add the wildcard to a Vercel-hosted project. Remove conflicting explicit public app records pointing to Tailscale `100.x` addresses; do not create a replacement public ComfyUI endpoint. Wait for Railway verification and certificates before testing from an off-tailnet browser. [Railway wildcard-domain instructions](https://docs.railway.com/networking/domains/working-with-domains)

## Acceptance checks before public use

1. Build and validate the image locally with placeholder variables (see below).
2. Test `GET /healthz` and verify unknown Host returns 404.
3. With no Authelia session, request Hermes `/`, API, assets, and WebSocket endpoints; none may reach the workstation. Test Authelia outage and confirm Hermes remains inaccessible.
4. Sign in with the intended account and second factor. Verify Hermes HTTP/API and WebSockets from an off-tailnet browser, including public Host and Origin checks.
5. Verify valid Railway certificates on the portal and Hermes subdomains, plus normal TLS validation on the private node backhaul.
6. Verify the former ComfyUI host returns 404 (except shared `/healthz`), edge-to-ComfyUI access is denied by Tailscale policy, and existing intended private clients retain access.

Local config check after `docker build -t caddy-railway:local .`:

```powershell
docker run --rm --entrypoint caddy `
  -e AUTH_HOST=auth.loon.day -e HERMES_HOST=hermes.loon.day `
  -e HERMES_TAILSCALE_HOST=desktop-friday.example.ts.net `
  -e HERMES_TAILSCALE_PORT=443 `
  -e AUTHELIA_UPSTREAM=authelia.railway.internal:9091 `
  -e TAILSCALE_PROXY_URL=http://tailscale.railway.internal:1055 `
  caddy-railway:local `
  adapt --config /etc/caddy/Caddyfile --adapter caddyfile --validate
```

## Automated local regression tests

Prerequisites: Python 3.10+, OpenSSL CLI, and Docker Desktop with Linux containers (`host.docker.internal` reachable from containers). No Python packages, Railway login, Tailscale enrollment, or real credentials are needed.

```sh
python -m unittest discover -s tests -v
```

The suite builds the actual pinned Dockerfile as `caddy-railway:hermes-only-test` (override the local tag with `EDGE_TEST_IMAGE`), runs the real entrypoint and Caddy, and validates both normal and maintenance configurations. It exercises missing/empty variables, duplicate hosts, legacy ComfyUI variables, health/unknown-host responses, portal and auth client-IP forwarding, denied/redirect/error/unreachable auth, HTTPS through HTTP CONNECT on default/custom backhaul ports, TLS hostname rejection, `PORT=443`, and authenticated WebSocket upgrades.

Fixtures use only generated one-day TLS certificates and reserved example names/addresses. The certificate is mounted only into test containers; production config never disables TLS verification. Docker's published edge ports bind loopback. Short-lived fixture listeners bind the host interfaces so Docker Desktop can reach them; the proxy tunnels only to the fixture app, never arbitrary hosts. Run on a trusted development machine. Containers and generated keys are removed on normal completion (including assertion failures); the local Docker image remains. Interrupting the process may require manual test-container cleanup.

These tests do not prove real Authelia MFA/OTP sessions, Railway header provenance/certificates, Tailscale enrollment/ACL enforcement, a live Hermes session, or sustained WebSocket frame transfer. Perform the acceptance checks above before public use. An unavailable private Tailscale service remains an operational blocker even when local tests and `/healthz` pass.

Use a real auth key only in Railway, not in a shell command retained in terminal history.
