# Soulbridge roadmap — request-platform expansion

Turning Soulbridge from a fulfilment engine (fed by AudioBookRequest) into a
self-contained request platform that can replace ABR. Phases are built and
reviewed in order; Soulbridge and ABR can run side by side until cutover.

## Locked decisions

- **Discovery source:** Audible catalog API (`api.audible.com/1.0/catalog/products`),
  region-aware, enriched by Audnexus (by ASIN). Same source ABR uses.
- **Request modes:** per request, **auto** (`pick_best` → grab) or **interactive**
  (show ranked Soulseek candidates with scores → user picks).
- **Auth:** internal accounts first; **Sign in with Plex** (Overseerr-style, verify
  membership of the configured Plex server) comes later as its own phase.
- **Roles:** `admin`, `trusted`, `standard`.
  - **standard** (default): requests require **admin approval**.
  - **trusted**: requests auto-download, no approval.
  - **admin**: full control.
- **Global controls (admin):** default role for new users (`trusted`/`standard`),
  and a per-user request **quota**. This instance will run default = `trusted`;
  the shipped default (for others) = `standard` (approval required).
- **Settings split:** **Server Settings** = admin only (connections, secrets, globals).
  **Account Settings** = every user (password, preferences, own request history).
- **No 2FA** for now (revisit with the Plex/OIDC login work).
- Secrets are **never rendered** back to the browser (write-only fields).

---

## Phase 1 — Auth + hardening  *(done — audited clean, v0.5.0)*

The security foundation. Nothing user-facing from later phases ships until this is done
and audited, because the app becomes internet-facing.

- Users table + roles; argon2id password hashing; first-run admin setup.
- Signed session cookies (httpOnly, SameSite=Lax, Secure when proxied via HTTPS);
  session regenerated on login; logout.
- **Auth required on every route** except login/setup/health/static.
- **Admin-only:** Server Settings, user management, connection/worker status, and the
  acquisition surface (manual search/grab, retries).
- **Account Settings** page for all users: change password, preferences.
- **User management** (admin): create/edit/delete users, set role, reset password;
  set global default role + quota.
- Secrets masked in Server Settings (write-only; blank = keep existing).
- **CSRF** tokens on all state-changing POSTs.
- **Login brute-force protection:** per-account lockout after N failures.
- **Security headers** (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy);
  inline scripts moved to static files so CSP can forbid inline JS.
- Deliverable: a security audit + report before proceeding.

## Phase 2 — Discovery search  *(done — v0.6.0)*

- `clients/audible.py` — catalog search (region-aware) → real listings (ASIN, title,
  authors, narrators, series, cover, runtime).
- Discovery UI (`/discover`, all users): search → results with **Request**.
- Request creates an item (source = `user`, ASIN, requested_by); trusted/admin →
  `pending` (auto-pipeline), standard → `awaiting_approval` (held for Phase 4).
- `My Requests` (`/requests`) shows each user their own request statuses.
- Nav reworked: user-facing Request / My Requests / Library first; admin extras after.

## Phase 3 — Auto vs interactive requests with scores  *(done — v0.7.0)*

- Per-request mode selector; configurable default (`default_request_mode` setting).
- Interactive: present ranked Soulseek candidate groups (user/format/size/free-slot/
  **score**/accept-reject) → user picks → grab, tagged by ASIN.
- Auto: `pick_best` → grab (existing behaviour).
- Interactive offered to trusted/admin users (who can auto-download); standard users'
  requests still route to approval regardless of mode — their interactive path lands
  with the Phase 4 approval queue.

## Phase 4 — Per-user history, approval, quotas

- Link items → requesting user; "My requests" (user) vs "All requests" (admin).
- Approval workflow for `standard` users: admin approve/deny queue.
- Per-user request quotas (global setting), enforced at request time.
- Audit log of request/approve/download actions.

## Phase 5 — Sign in with Plex

- Plex PIN OAuth (`plex.tv/api/v2/pins` → authorise → poll → account).
- Verify membership of the configured Plex server (only your users get in);
  auto-provision Soulbridge users (default role from global setting).
- Internal accounts remain for admin / non-Plex users.

## Phase 6 — Parity polish & cutover

- Notifications (Apprise) on request/approval/completion.
- Request status visible to the requester.
- Optional: import existing ABR request history.
- Retire ABR once satisfied.

---

## Status log

- 2026-08-22: plan agreed; Phase 1 started.
- 2026-08-22: Phase 1 complete (v0.5.0) — internal accounts, roles, sessions, CSRF,
  admin-gating, masked secrets, lockout, security headers/CSP. Security audit: no
  HIGH/MEDIUM findings. Deployed; instance now requires first-run admin setup.
- 2026-08-22: Settings UX (dropdown role, category side-nav + scrollspy, floating
  glow-on-change save) and status indicators moved to a bottom toolbar (v0.5.1).
- 2026-08-22: Phase 2 complete (v0.6.0) — Audible discovery search, request flow
  (role-aware: trusted auto, standard awaiting-approval), My Requests page.
- 2026-08-22: accent-folding fix (v0.6.1) — "recursión" → "recursion" in search
  queries (NFKD + ligature map).
- 2026-08-23: Phase 3 complete (v0.7.0) — per-request auto/interactive mode with a
  configurable default; interactive picker shows ranked Soulseek candidates + scores
  (`selecting` status the worker skips; `/request/{id}/candidates` + `/pick` + `/auto`,
  ownership-checked). Fixed ASIN tagging to fire for in-app `user` requests, not just
  `abr`. Interactive is trusted/admin-only for now (standard → approval, Phase 4).
- **NEXT: Phase 4** (per-user history, approval queue, quotas). Standard-user requests
  already land as `awaiting_approval`; build the admin approve/deny UI, enforce
  `default_role`/`request_quota`, and honour the stored request `mode` on approval.
