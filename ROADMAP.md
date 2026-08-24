# Soulscribe roadmap — request-platform expansion

Turning Soulscribe from a fulfilment engine (fed by AudioBookRequest) into a
self-contained request platform that can replace ABR. Phases are built and
reviewed in order; Soulscribe and ABR can run side by side until cutover.

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
  Soulscribe's own `/discover` + requests are the front door). Soulscribe is
  self-sufficient without ABR.

---

# Next epoch — Soulscribe as an audiobook management suite

All six original phases are done; Soulscribe has met and outgrown its founding
goal. The next epoch turns it from a request-and-fulfil pipeline into a
Soulseek-first audiobook **management suite**: tunable matching, multi-source
acquisition (Prowlarr), real library management (leaving Audiobookshelf as
delivery only), and discovery that knows what you actually want next. Full
audit + rationale: see the 2026-08-24 audit (kept as project history, not
duplicated here).

## Locked decisions for this epoch

- **Sequencing:** Phase 0 (hardening) → Phase 7 (tunable matching) → Phase 8
  (discovery Tier 1 + follows) → Phase 9 (multi-source acquisition) →
  Phase 10 (library management + discovery Tier 2). The two load-bearing
  design decisions — the provider/downloader abstraction (Phase 9) and the
  `books`/`editions` data model (Phase 10) — come with enough of the rest
  built first that they can be done right instead of fast.
- **eBooks are out of scope.** Audiobooks only, for now.
- **New account creation:** allow public UI sign-up (for non-Plex users),
  landing as `awaiting_admin_approval` — an admin must approve before the
  account can log in. Keeps the "no open signup without a gate" posture
  Sign-in-with-Plex already has, without requiring Plex.
- **Prometheus `/metrics`:** admin-gated or token-gated (not public) — decide
  the exact exposure in Phase 0 hardening's admin-path work, so it doesn't
  leak operational detail (queue depth, connected services) to the internet.

## Phase 0 — Foundation hardening  *(done — v0.15.x)*

Not user-facing; makes every later phase in this epoch cheaper and safer.

- Rebrand Soulbridge → Soulscribe (repo, package, image, UI) with backward
  compatible `SOULBRIDGE_*` env vars and DB filename for the upgraded
  instance.
- CI test gate: `pytest` runs on every PR and gates the Docker build/publish.
- `PRAGMA user_version` migration framework in `db.py`, replacing ad-hoc
  column checks — future schema work (Phase 10's `books`/`editions` tables)
  appends a migration instead of touching `init()`.
- Unified `cache.TTLCache` — the five scattered module-dict caches
  (`_RESULTS_CACHE`, `_SEARCH_CACHE`, `_PLEX_PENDING`, `_LIB_INDEX`,
  `_BROWSE`) now share one implementation; single-process assumption
  documented in the module docstring.
- `server.py` split from 977 lines into `web/common.py` + `web/routes/*`
  (auth, users, dashboard, manual, requests, library, settings_routes),
  behaviour-preserved and verified by a new route/auth characterisation
  test suite (`tests/test_routes.py`).

## Phase 7 — Tunable matching  *(core done — v0.16.1)*

- Promote `score_group()`'s hard-coded weights and keyword lists (`SPAM`,
  `DRAMATIZED`, `MUSIC_DIRS`, `AUDIOBOOK_MARKERS`) into a "Matching" settings
  group, defaulted to today's values. Curated presets (Balanced / Prefer
  single M4B / Prefer highest bitrate / Lenient) so tuning doesn't require
  understanding the point values.
- Score breakdown surfaced in the interactive candidate picker (`{reason:
  points}`) so a weight change has visible cause and effect.
- Guardrail: hard rejects (coverage floor, SPAM, blocklist, size bounds) stay
  rejects regardless of user-set weights; editable weights are range-clamped.
- **Deferred (future): "test against last search" preview** on the settings
  page — re-rank the most recent cached Soulseek responses with pending
  (unsaved) weights before saving. Not yet built; the score-breakdown UI
  covers the "why did this score X" need for now, so this is a nice-to-have
  rather than a gap.

## Phase 8 — Discovery Tier 1 + follows  *(discovery + follows done — v0.18.0)*

- **Series completion** hero row: cross-reference the ABS library against
  series membership (via Audible's `sims?similarity_type=InTheSameSeries` /
  `NextInSameSeries` and the `relationships` response group) and surface
  "Complete the series — you're missing books 3 & 4." Ships against today's
  schema; no taste model needed. ✅ done (v0.17.0) — plus "More from authors
  you own" (`ByTheSameAuthor`-equivalent search) and "Because you have X"
  (`RawSimilarities`), reusing `_mark_results()`.
- **Author/series follow → auto-request new releases**, gated by role same as
  any other request (approval for `standard`, auto for `trusted`/`admin`).
  ✅ done (v0.18.0) — `core/library.py` extracted as the shared "already own
  this" seam (used by discovery *and* follows) so Phase 10's books/editions
  model has one place to update, not several; `follows` table + worker
  `_check_follows()`/`_process_follow()`; deliberately not quota-limited.
- Requester-visible download progress (%, not just status) on My Requests,
  sourced from slskd/download-client transfer state.
- Generalise the existing "Wrong book?" mismatch button into "Try a different
  source" — retry against the next-best candidate without blocklisting
  (mismatch blocklisting stays for actually-wrong content).
- Wishlist / "notify when available": a `no_results`-parked book can opt into
  periodic low-frequency re-search instead of staying terminally parked,
  notifying when it's finally found — properly integrated with the existing
  `scheduled`/pre-save flow rather than a separate concept.

## Phase 9 — Multi-source acquisition (Prowlarr, qBittorrent, SABnzbd)

- Provider/downloader abstraction: `Indexer.search(...) → [Candidate]` and
  `Downloader.enqueue/state/completed_path`. Refactor the Soulseek path as the
  first implementation (`SoulseekProvider` wrapping `slskd` + `matching.py`)
  to prove the abstraction without changing behaviour.
- `ProwlarrProvider` (Torznab, category 3030/Audio-Audiobook) +
  `QbittorrentDownloader` / `SabnzbdDownloader`.
- Per-provider scorers on a comparable scale (`score_release()` for Torznab,
  reusing the title/author/edition/book-number helpers already in
  `matching.py`, plus seeders/freeleech/size) and a source-preference policy
  setting (Soulseek-first / Prowlarr-first / best-score-wins /
  Prowlarr-for-edition-upgrades).
- `items.provider` + `items.download_client` columns; interactive picker
  shows candidates from all sources with a source badge.
- Scope discipline: Soulscribe queries Prowlarr and hands off to download
  clients — it does not manage indexer definitions, RSS sync, or release
  profiles (Prowlarr already does that).

## Phase 10 — Library management + Discovery Tier 2

The keystone phase: split the `items` table's conflation of "a request" from
"a book in the library."

- New `books` table (canonical work: title, author, series + position, wanted
  edition, owned edition, monitored flag, cutoff state, library path) and an
  `editions` model (narrator, year, format, abridged/dramatised, ASIN) —
  persisting what `build_editions`/`pick_edition` already compute today.
  `items`/requests become acquisitions against a book.
- Metadata profiles (allowed languages, format preference, abridged/
  dramatised policy) turning the existing detection primitives (`DRAMATIZED`,
  `_LANG_MARKERS`) into configurable intent instead of per-grab surprise.
- In-UI metadata editing + re-tag/refresh-from-Audnexus on demand.
- Edition tracking + quality/edition upgrade logic ("cutoff met?" — replace a
  64kbps MP3 pile with a single M4B when a better copy appears).
- File organisation beyond the folder: chaptered naming, multi-part
  (`Part 1/2/3`) safeguards — the deferred v0.11.0 rename work, now properly
  scoped against real edition/part data.
- ABS write-back: document + default to ABS trusting local metadata (embedded
  tags + local metadata file) so Soulscribe is the metadata authority and ABS
  is a pure renderer — the "ABS as delivery only" goal.
- **Discovery Tier 2**: personalised recommendations from request history +
  ABS library (and ABS listening/progress data where available), ranking
  `sims` candidates against a lightweight taste profile. Every recommendation
  carries an explainable reason chip.
- Bulk admin operations (approve-all, retry-all-failed, re-tag-all-in-series)
  — trivial once routes are router-split and this data model exists.

## Also planned (not yet phased)

- **Public account sign-up** with admin-approval gate (see locked decisions
  above), alongside the existing Sign-in-with-Plex path.
- **OIDC / forward-auth support** (SWAG/Authelia/Tailscale-style headers),
  revisiting the "no 2FA yet" note from Phase 5 now that there's a second
  external-identity path.
- **Prometheus `/metrics`** — queue depth, success rate, worker health;
  exposure gating decided in Phase 0's admin-path hardening.
- **CHANGELOG.md**, split out from this status log, tied to release tags.

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
- 2026-08-24: Broader dramatised detection (v0.14.2) — added foreign/extra markers to
  `matching.DRAMATIZED` (Hörspiel, dramatizada, dramatisée, vollvertont, dramatization…) and folded
  the narrator tag into the check, so Red Rising's "[Dramatized Adaptation] / Full Cast" and Fourth
  Wing's German "Das Hörspiel" dramatised editions are recognised. Verified against both series'
  real Audible listings.
- 2026-08-24: Dramatised/Graphic Audio edition flagging (v0.14.1) — the Audible catalog API doesn't
  return Graphic Audio editions (verified: even "mistborn graphic audio" returns only the standard
  listing), so they can't be found by lookup. Instead the post-download check now recognises a
  dramatised/Graphic Audio rip from the file's own title/genre tags (+ folder/filenames), flags it
  "Alternate edition: dramatised / Graphic Audio version", and tags it conservatively so it keeps
  its real title. (Split Part 1/Part 2 editions remain a known hard case; foreign-language
  alternates are covered by the existing language warning.)
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
  CSP. Hardening added: opt-in Secure session cookies (`SOULSCRIBE_SECURE_COOKIES`) and
  constant-time login for unknown usernames. No HIGH/MEDIUM findings.
- 2026-08-24: Codebase audit — architecture/hygiene review plus scoping for the four
  requested next-epoch features (tunable matching, Prowlarr multi-source, library
  management, smart discovery) and eight additional proposals. Verdict: strong
  foundation, four features are the right move, sequencing matters (provider
  abstraction and the books/editions data model are the two load-bearing decisions).
  Next-epoch plan written up above as Phases 7–10.
- 2026-08-24: Rebrand Soulbridge → Soulscribe (v0.15.0) — new quill-and-book identity;
  package, repo (`Natrosity/soulbridge` → `Natrosity/soulscribe`), image, and all UI/docs
  renamed. Backward compatible: `SOULSCRIBE_*` env vars fall back to legacy
  `SOULBRIDGE_*`, and an existing `soulbridge.db` keeps being used in place rather than
  stranding it.
- 2026-08-24: Phase 0 (foundation hardening) complete — CI now runs `pytest` on every PR
  and gates the Docker build; `db.init()` migrated from ad-hoc column checks to a
  `PRAGMA user_version` migration framework; five scattered module-dict caches unified
  into `cache.TTLCache`; `server.py` split from 977 lines into `web/common.py` +
  `web/routes/*`, verified behaviour-preserving by a new route/auth characterisation
  suite (`tests/test_routes.py`). Sets up Phases 7–10 to be built on a steadier base.
- 2026-08-24: Phase 7 core (v0.16.0) — `score_group()`'s ~20 weights and four keyword
  lists are now settings-backed (`weight_*`/`keyword_*`, a new "Matching" settings group)
  instead of hard-coded, with four curated presets and server-side range clamping.
  Behaviour is unchanged until a setting is edited (all pre-existing matching tests pass
  untouched). Caught a real bug live in a browser — `bitrate_cap`'s default wasn't a
  multiple of its own HTML `step`, so native constraint validation silently blocked the
  whole settings form from submitting; fixed, with a permanent regression test.
- 2026-08-24: Phase 7 breakdown UI (v0.16.1) — `score_group()` gained an `explain` param;
  `worker.manual_search` now attaches a `{label, points}` breakdown to every candidate,
  shown as a native `<details>` disclosure on the score in `/search` and the interactive
  candidate picker (no custom JS). Verified live by monkeypatching `manual_search` and
  driving `/search` in a real browser (no slskd available locally). "Test against last
  search" preview remains deferred (see Phase 7 above).
- 2026-08-24: Phase 8 discovery Tier 1 (v0.17.0) — three personalised `/discover` hero
  rows seeded from the ABS library: "Complete the series" (Audible `sims`
  `InTheSameSeries`), "More from authors you own", "Because you have X" (`sims`
  `RawSimilarities`). New `Audible.similar()` client method + a shared `_normalize()`;
  verified the `sims` response envelope (`similar_products`, not `products`) live before
  writing consuming code. Verified live end-to-end (mocked ABS, real Audible API,
  real request round-trip). One observation, not a bug: Audible sometimes lists multiple
  ASINs for the same edition, so a missing-books row can occasionally surface an edition
  variant of an owned book — consistent with how the app already treats editions.
- 2026-08-24: Phase 8 follows (v0.18.0) — follow an author or series; new `/follows` page
  + `core/worker._check_follows()` auto-requests new releases going forward (role-gated,
  not quota-limited, no backlist flood — only releases dated on/after the follow's
  creation count). Extracted `core/library.py` as the shared "already own this" check
  (used by both discovery heroes and the follow-checker) specifically so Phase 10's
  books/editions model has one seam to update later, not several — a deliberate call made
  after being asked to keep Phase 10 compatibility in mind while building this. New
  `Audible.by_author()` (a dedicated `author=` filter, more precise than keyword search —
  verified live). Caught a real bug live: Audible's catalog uses a placeholder release
  date (`2200-01-01`) on some listings, which would have created a `scheduled` request
  that can never come due; added a plausibility guard, re-verified against the real API.
  Requester-visible progress, generalised retry, and the wishlist/notify-when-available
  flow are still open from Phase 8 (see above).
