# Railway edge for Hermes Agent and ComfyUI

This fork adapts the [official Caddy Railway template](https://caddyserver.com/docs/quick-starts/railway) for two public, authenticated app subdomains. Railway terminates browser HTTPS. Caddy checks each request with Authelia, then reaches the Windows workstation through a private Tailscale connection. The home router does not need a port forward.

```text
Browser -> Railway TLS -> this Caddy service -> Authelia (Railway private network)
                                            -> Tailscale proxy (Railway private network)
                                            -> private Tailscale Services -> Hermes / ComfyUI
```

## What this repository provides

- Routes `HERMES_HOST` and `COMFY_HOST` to separate private Tailscale Service hostnames, each on HTTPS port 443.
- Requires Authelia `forward_auth` before every app request. If Authelia fails, the app proxy does not run.
- Returns 404 for the Railway-generated URL and unknown hosts. `/healthz` is the only public route that skips authentication.
- Uses Caddy's built-in `encode zstd gzip`, HTTP streaming, WebSocket proxying, and HTTP network proxy transport. No Caddy plugin is needed. The experimental Tailscale Caddy plugin crashed during configuration validation, so this deployment uses the [official Tailscale container](https://tailscale.com/docs/features/containers/docker/docker-params) in userspace mode as a separate private Railway service.

The previous VPS plan's public DNS, TLS, and edge host steps are replaced by Railway. Its private Windows Serve routes and MFA requirements still apply.

## Prerequisites

1. A Railway project with this Caddy service and a separate Authelia service. Use persistent Authelia storage and configure a user, TOTP or WebAuthn, recovery codes, and session cookies for your chosen domain. Do not place Authelia secrets or user database in this repository. See the [Authelia Caddy integration](https://www.authelia.com/integration/proxies/caddy/).
2. Tailscale MagicDNS and HTTPS enabled for the existing `svc:hermes` and `svc:comfyui` services. Give the Railway Tailscale service an ephemeral auth key or OAuth client secret that can register `tag:railway-edge`; add grants allowing that tag to reach **only** those two services on port 443. Keep the key in Railway variables, never in Git. See [Tailscale grants](https://tailscale.com/docs/features/access-control/grants).
3. Hermes Agent and ComfyUI answering behind those private Tailscale Services. The service target ports on the Windows machine must have active listeners. Do not point public DNS at a Tailscale `100.x` address.

## Deploy the Caddy service

Connect this fork's branch or merged `main` to the existing Railway Caddy service in **Settings → Source**. If it is still tied to the template, use Railway's **Eject** action as described in the [Caddy Railway guide](https://caddyserver.com/docs/quick-starts/railway), then point the service at this fork. Set the service's public target port to `8080`, or set `PORT` and the target port to the same value. Remove the template's `CADDY_PLUGINS` variable; this image uses stock Caddy 2.11.4.

Set these Railway variables on the Caddy service:

| Variable | Example | Purpose |
| --- | --- | --- |
| `HERMES_HOST` | `hermes.loon.day` | Public Hermes hostname, without scheme |
| `COMFY_HOST` | `comfy.loon.day` | Public ComfyUI hostname, without scheme |
| `HERMES_TAILSCALE_HOST` | `hermes.<tailnet>.ts.net` | Private Hermes Service hostname, without scheme or port |
| `COMFY_TAILSCALE_HOST` | `comfyui.<tailnet>.ts.net` | Private ComfyUI Service hostname, without scheme or port |
| `TAILSCALE_PROXY_URL` | `http://tailscale.railway.internal:1055` | Internal URL of the Tailscale service's outbound HTTP proxy |
| `AUTHELIA_UPSTREAM` | `authelia.railway.internal:9091` | Authelia service's private Railway address and port |
| `PORT` | `8080` | Optional; must match Railway's target port |

The container refuses to start if a required variable is missing or both app hostnames are equal. It exposes `/healthz` for Railway's health check; that check confirms Caddy is listening, **not** that Authelia or the Windows workstation is healthy.

Configure the separate Authelia service's public portal at `auth.loon.day` and its session cookie domain for `loon.day`. Configure Authelia's access policy to require a second factor for both app domains. Keep its internal port private to Railway. The Authelia portal itself needs a public Railway domain so an unauthenticated browser can sign in.

## Deploy the private Tailscale proxy service

Add another Railway service in the **same project and environment**, using the official `tailscale/tailscale:v1.102.2` Docker image. Do not add a public domain or TCP proxy. Name it `tailscale` so its internal DNS name is `tailscale.railway.internal`, or update `TAILSCALE_PROXY_URL` accordingly. Set these variables on that service:

| Variable | Value | Purpose |
| --- | --- | --- |
| `TS_AUTHKEY` | Railway secret | Ephemeral auth key or OAuth client secret |
| `TS_EXTRA_ARGS` | `--advertise-tags=tag:railway-edge` | Stable identity for tailnet grants |
| `TS_ACCEPT_DNS` | `true` | Accept tailnet DNS settings for the service hostnames |
| `TS_USERSPACE` | `true` | No TUN device or privileged container needed |
| `TS_OUTBOUND_HTTP_PROXY_LISTEN` | `:1055` | Caddy connects through Railway private networking |
| `TS_HOSTNAME` | `railway-edge` | Recognizable tailnet node name |

Use an **ephemeral** credential because Railway's container filesystem is not persistent. The official image supports this userspace HTTP proxy mode. If you later attach a Railway volume, you may instead persist Tailscale state and use `TS_AUTH_ONCE=true`. The proxy port must remain private to this Railway project; it has no per-request authentication of its own. [Tailscale userspace networking](https://tailscale.com/docs/concepts/userspace-networking), [Railway private networking](https://docs.railway.com/networking/domains/working-with-domains)

## Windows private backhaul

The Windows machine already hosts `svc:hermes` and `svc:comfyui` as private Tailscale Services. Confirm `tailscale serve status` shows both routes as **tailnet only**, not Funnel. Each service must proxy to a live local app port. Test `https://<HERMES_TAILSCALE_HOST>` and `https://<COMFY_TAILSCALE_HOST>` from an allowed tailnet device with normal certificate verification. A disallowed tailnet device and an ordinary internet client must be unable to reach them directly.

Hermes checks the HTTP Host and WebSocket Origin values. Configure `dashboard.public_url: https://hermes.loon.day` (or `HERMES_DASHBOARD_PUBLIC_URL`) and an auth provider before exposing the dashboard. The Caddyfile forwards the public Host while validating TLS against the private Tailscale hostname. After restarting Hermes, verify `/api/status` reports `auth_required: true` and an intended provider. A Desktop-owned loopback backend may be exempt from this gate even with a public URL; do not route such a backend publicly. [Hermes dashboard documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/web-dashboard.md)

## Custom domains and DNS

Attach `hermes.loon.day` and `comfy.loon.day` to this Caddy service in Railway. Attach `auth.loon.day` to the Authelia service. Railway Hobby permits two custom domains per service, which is why the portal uses its own service. For each hostname, copy the exact **CNAME and ownership-verification TXT** records Railway provides into Vercel DNS, then wait for Railway verification and certificates. The current public A records for these subdomains point to a Tailscale `100.x` address; replace them with Railway's records before testing from an off-tailnet browser. [Railway custom-domain instructions](https://docs.railway.com/networking/domains/working-with-domains)

## Acceptance checks before public use

1. Build and validate the image locally with placeholder variables (see below).
2. Test `GET /healthz` and verify unknown Host returns 404.
3. With no Authelia session, request each app's `/`, API, assets, and ComfyUI `/ws`; none may reach the workstation. Test Authelia outage and confirm the apps remain inaccessible.
4. Sign in with the intended account and second factor. Verify Hermes HTTP/API and ComfyUI pages, WebSockets, uploads, and image downloads from an off-tailnet browser. Verify any Hermes origin or WebSocket host checks with the public hostname.
5. Verify valid Railway certificates on all three subdomains and normal TLS validation on both `*.ts.net` backhaul ports.
6. Check Railway's [public networking limits](https://docs.railway.com/networking/public-networking/specs-and-limits) against ComfyUI uploads and long running generations. Avoid a caching plugin for private images and prompts.

Local config check after `docker build -t caddy-railway:local .`:

```powershell
docker run --rm --entrypoint caddy `
  -e HERMES_HOST=hermes.loon.day -e COMFY_HOST=comfy.loon.day `
  -e HERMES_TAILSCALE_HOST=hermes.example.ts.net `
  -e COMFY_TAILSCALE_HOST=comfyui.example.ts.net `
  -e AUTHELIA_UPSTREAM=authelia.railway.internal:9091 `
  -e TAILSCALE_PROXY_URL=http://tailscale.railway.internal:1055 `
  caddy-railway:local `
  adapt --config /etc/caddy/Caddyfile --adapter caddyfile --validate
```

Use a real auth key only in Railway, not in a shell command retained in terminal history.
