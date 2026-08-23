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

## Phase 4 — Per-user history, approval, quotas  *(done — v0.8.0)*

- Link items → requesting user; "My requests" (user) vs "All requests" (admin, `/requests/all`).
- Approval workflow for `standard` users: admin approve/deny queue with a nav count badge.
  Approve → `pending` (auto-grab); deny → `denied`. (Standard users' items are stored
  `mode=auto`, so there's no interactive-picker step on approval.)
- Per-user request quotas (`request_quota`), enforced at request time for non-admins
  (0 = unlimited); a friendly banner explains the block. Re-requesting an existing book
  is an upsert and never trips the quota.
- Audit log of request/approve/deny/download actions (the events log).

## Phase 5 — Sign in with Plex  *(done — v0.9.0)*

- Plex PIN OAuth (`plex.tv/api/v2/pins` → authorise at app.plex.tv → poll → account),
  in `clients/plextv.py`; a stable per-install client id is stored in settings.
- Verify membership of the configured Plex server (via the account's `resources`
  matching the server's `machineIdentifier`); only accounts that can reach the server
  are admitted. Auto-provision users at the global `default_role`, matched by `plex_id`
  only (a matching username can't hijack an internal account — it gets uniquified).
- Internal accounts remain for admin / non-Plex users (Plex users have no password).
- Enabled by `plex_login_enabled` (needs Plex URL + token). `public_url` sets the
  external base for the OAuth redirect (falls back to the request host).

## Phase 6 — Parity polish & cutover  *(done — v0.10.0)*

- Notifications (Apprise) on request / completion / failure, with per-event toggles
  and a "Send test notification" button. Best-effort, fire-and-forget on a daemon
  thread so a notifier hiccup never breaks the pipeline (`clients/notify.py`).
- Request status visible to the requester (My Requests) — from Phase 2/4.
- Import existing ABR request history (admin button on `/requests/all`): past
  fulfilments become library records, outstanding ones queue — dedup via upsert.
- Retire ABR: an operational step for the operator (clear the ABR URL/key once
  Soulbridge's own `/discover` + requests are the front door). Soulbridge is
  self-sufficient without ABR.

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
- 2026-08-23: Phase 4 complete (v0.8.0) — admin approval queue at `/requests/all`
  (approve→pending / deny→denied) with a nav count badge; per-user open-request quota
  enforced at request time for non-admins with a friendly banner; audit events for
  approve/deny. `db.count_open_requests` + `OPEN_STATUSES`; `denied` status.
- 2026-08-23: Phase 5 complete (v0.9.0) — Sign in with Plex (PIN OAuth in
  `clients/plextv.py`, membership gate via server machineIdentifier, provision by
  plex_id at default_role, login button behind `plex_login_enabled`, `public_url`
  for the redirect). Verified live (PIN round-trip + provisioning); real OAuth
  round-trip needs a human, so give it a real sign-in before cutover.
- 2026-08-23: Phase 6 complete (v0.10.0) — Apprise notifications (request/complete/
  failure + test button; new `apprise` dep → image rebuild), ABR history import,
  cutover-ready. All six phases shipped; ABR retirement is now an operator choice.
- 2026-08-24: Alternate-edition detection + liberal tag cleanup (v0.14.0) — post-download, read the
  file's own narrator/year (composer + year tags) and, if they match a different edition of the same
  book (via Audible alternate-edition lookup), tag it as that edition and flag "Alternate edition"
  in the history (`items.note`) instead of mislabelling it as the requested one. Also, once matched
  to an edition, title/album/narrator/genre/year/description tags are replaced outright (not just
  superset-enriched) via `tagging.decide_field` + `AUTHORITATIVE`. Verified alternate detection on
  real data (The Hobbit → Inglis/Serkis/Shaw editions).
- 2026-08-24: Bitrate tiebreaker + series book-number signal (v0.13.2) — among otherwise-equal
  candidates the higher-bitrate rip now wins (bounded +0–3.2, a pure tiebreaker; bitrate shown in
  the picker). Plus a book-number signal: a candidate that names the requested series position gets
  a boost and a different number a penalty, so requesting Mistborn (Book 1) now ranks the Final
  Empire copies clearly above the un-numbered Era-2 books that the sibling check can't reject.
- 2026-08-24: Series disambiguation (v0.13.1) — a book whose title collides on the series name
  (e.g. "Mistborn" = Book 1) no longer grabs a different entry ("The Alloy of Law", Book 4) that
  shares the series name, narrator, and year. Fetches the series' sibling books from Audible
  (`matching.build_siblings`, cached per ASIN) and rejects a candidate whose files clearly name a
  different numbered entry. Verified live on the real "Mistborn" request (Final Empire now top,
  Alloy of Law rejected).
- 2026-08-23: Mismatch protection + blocklist (v0.13.0) — post-download metadata analysis reads the
  downloaded audio (mutagen) and, if it's music (too short, music genre tag, or a short-track album),
  rejects it *before* import, blocklists that Soulseek upload, and re-queues. Users get a "Wrong
  book?" button on completed requests that does the same (and removes the imported files); admins
  see and can clear the blocklist on /requests/all. The matcher skips blocklisted (user, dir) pairs.
- 2026-08-23: Matching refinement (v0.12.2) — content-type awareness (music path/album vs audiobook)
  and a stronger generic-title guard (<=2 distinctive words require the author). Fixed short-title
  grabs pulling songs / wrong-author books.
- 2026-08-23: Timezone support + region-correct Audible links (v0.12.1) — all timestamps
  now respect the `TZ` env var (via `zoneinfo`; `db.now()`/`db.today()` replace the old UTC
  `gmtime` calls) instead of hardcoded UTC. Audible product URLs already followed the
  configured `audible_region`, so region-specific ASINs (e.g. an AU-only book) now link to
  the matching domain — set this instance's region to `au`. Added `tzdata` dep for slim-image
  robustness (the running container already has system tzdata).
- 2026-08-23: Discovery/library polish + edition matching (v0.12.0) — hero rows reworked
  (Bestsellers = released only, "Releasing Soon" = upcoming only; request/Pre-Save button
  overlaid on the cover, revealed on hover / always on touch; cover + title link to the
  Audible page; themed scrollbars). Library cards link to Audible. **Fixed a cover bug**
  (an earlier edit had turned `ABS.item_cover` into dead code, so all library covers 404'd).
  **Edition matching**: the ranker now favours the requested narrator + year and avoids
  grabbing a dramatised/full-cast upload when a standard edition was requested (and vice
  versa); on import it logs a warning on an obvious language or edition mismatch.
- 2026-08-23: Post-launch enhancements (v0.11.0) — (1) **library dedup**: discovery
  now cross-checks the real ABS library (ASIN + title|author-surname key, cached 5 min)
  and flags already-owned books ("In your library", no request button); (2) **library
  search + sort** (ABS search endpoint + sort by title / author surname / recently
  released / recently added); (3) **discovery hero rows** — cached Audible browse
  ("Bestsellers", "New & upcoming"), one-click request; (4) **release-date gating** —
  future-dated requests land as `scheduled` and the worker (`_release_due`) only searches
  once the release date passes. New `items.release_date` column.
  - **Deferred (future): file renaming** — rename/organise the actual downloaded audio
    files (not just the folder). Needs a proven naming format AND smart safeguards so
    multi-file books don't get chapters mis-ordered/mislabelled; parked until that's
    designed. (ABR-sourced unreleased books aren't release-gated yet — ABR is retiring.)
- 2026-08-23: Final audit (v0.10.1) — all six phases done, then a functionality/polish/
  security pass. Verified live: every POST is CSRF-protected; admin surfaces reject
  standard users (GET + POST); unauthenticated is bounced; request ownership is enforced
  (candidates/pick/auto); no XSS (`|safe`-free, autoescape on); no secrets in logs; strict
  CSP. Hardening added: opt-in Secure session cookies (`SOULBRIDGE_SECURE_COOKIES`) and
  constant-time login for unknown usernames. No HIGH/MEDIUM findings.
