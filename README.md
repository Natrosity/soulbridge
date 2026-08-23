<p align="center">
  <img src="assets/logo.png" alt="Soulbridge" width="160">
</p>

<h1 align="center">Soulbridge</h1>

<p align="center">Request an audiobook, get it from Soulseek, filed and tagged for your library.</p>

---

Soulbridge is a self-hosted **request platform for audiobooks**, the way Overseerr is for movies and
TV. People browse and request titles in a clean web UI; Soulbridge finds the best copy on the
[Soulseek](https://www.slsknet.org/) network via [slskd](https://github.com/slskd/slskd), downloads
it, organises it into `Author/Title`, and tags it from the Audible listing so it lands clean in
**Plex**, **Jellyfin**, and **Audiobookshelf**.

It can also run behind [AudioBookRequest](https://github.com/markbeep/AudioBookRequest) or the *arr
stack, fulfilling their requests in the background, so you can adopt it gradually or drop them
entirely.

It exists because audiobook coverage on torrent and Usenet indexers is thin, while Soulseek is full
of them, and nothing tied a proper request workflow to it.

## How it works

1. A user searches Audible from the **Request** page (or a request arrives from AudioBookRequest).
2. Depending on their role the request is auto-fulfilled or held for admin approval.
3. Soulbridge builds a clean Soulseek query, ranks the responses (format, size, free slot,
   title/author match, narrator, edition, unabridged), and downloads the best one through slskd.
4. When the download finishes it moves the files into `Author/Title`, tags them from the Audible
   metadata, and tells your media servers to scan the new folder.

## Features

### Requesting

- **In-app discovery:** search the Audible catalog and request with a click. Region-aware.
- **Hero rows:** curated **Bestsellers** and **Releasing Soon** shelves on the request page.
- **Auto or interactive:** requests either grab the best Soulseek match automatically, or let the
  user pick from ranked candidate sources (user, format, size, free slot, score).
- **Library-aware:** books you already own (checked against Audiobookshelf by ASIN and
  title/author) are flagged instead of offered, so you can't request a duplicate.
- **Release-date aware:** a not-yet-released title is scheduled and only searched for once its
  publication date passes.
- **My Requests:** every user sees the live status of their own requests.

### Matching & fulfilment

- **Smart Soulseek matching:** title-first queries, summary/sample filtering, format and size
  preferences, a bias toward single-file M4Bs, and free-slot awareness. Generic one-word titles
  require the author to match so "Fire" doesn't pull a music track. When several candidates are
  otherwise equal, the higher-bitrate rip wins.
- **Edition & series awareness:** ranking favours the requested narrator and release year, and
  avoids grabbing a dramatised/full-cast upload when a standard edition was requested (and vice
  versa). For books in a series it recognises the sibling entries, so requesting "Mistborn" (Book 1)
  won't pull "The Alloy of Law" just because both share the series name. On import it warns about an
  obvious language or edition mismatch.
- **Mismatch protection:** short, common titles no longer pull songs or the wrong author. After a
  download, Soulbridge reads the file metadata and rejects music (by duration and genre) instead of
  filing it; users can also flag a completed request as the wrong content. Either way the bad
  upload is blocklisted and a different copy is tried.
- **Self-organising:** files into `{author}/{title}` (configurable). No Readarr required.
- **Audible tagging:** rewrites tags and cover art from the Audible listing (via
  [Audnexus](https://audnex.us)) and tidies up messy title/album/narrator/genre/year/description
  tags. If a download's own tags reveal it's actually a *different edition* of the book (another
  narrator or year), it's tagged as the edition it really is and marked **"Alternate edition"** in
  your history, rather than mislabelled as the one you asked for.
- **Targeted media-server scans:** Plex scans just the new folder, not the whole library; Jellyfin
  and Audiobookshelf too, with live connection indicators for each.

### Accounts & access

- **Roles:** `admin`, `trusted`, and `standard`. Standard users' requests need admin approval;
  trusted users auto-download; admins run the show.
- **Approval queue:** admins approve or decline pending requests, with a live count in the nav.
- **Per-user quotas:** cap how many open requests a non-admin can have.
- **Sign in with Plex:** Overseerr-style PIN login that verifies membership of your Plex server and
  auto-provisions users at the default role. Internal accounts remain for admins and non-Plex users.
- **Hardened:** argon2 password hashing, signed sessions, CSRF on every form, login lockout, a
  strict Content-Security-Policy, and secrets that are write-only in the UI (never rendered back).

### Everything else

- **AudioBookRequest integration:** polls approved requests, marks them done when they land, and can
  import existing request history in one click.
- **Notifications:** [Apprise](https://github.com/caronc/apprise) alerts on request, completion, and
  failure (Discord, Telegram, ntfy, email, and many more), with a test button.
- **Library browser:** a grid of what you already own from Audiobookshelf, with search and sort;
  covers are proxied so no token leaves the server.
- **Web UI:** live dashboard, manual search and grab, tag-write history, per-user account settings,
  and admin server settings. No config files needed.
- **Light:** FastAPI and SQLite in a single container.

## Quick start

```bash
docker run -d --name soulbridge \
  -p 8793:8793 \
  -e TZ=Australia/Brisbane \
  -v $PWD/config:/config \
  -v /path/to/data:/data \
  ghcr.io/natrosity/soulbridge:latest
```

Open `http://localhost:8793`, create the first admin account, then go to **Server Settings** and
point it at slskd (and optionally AudioBookRequest, Plex, Jellyfin, Audiobookshelf). See
[`docker-compose.example.yml`](docker-compose.example.yml) for a fuller setup.

> Mount the same parent directory that holds both your slskd downloads and your audiobook library
> (e.g. `/data`), so finished files move into place on one filesystem.

**Going public?** Soulbridge is safe to put behind an HTTPS reverse proxy (auth is required on every
route). Set `public_url` so the Plex sign-in redirect comes back over HTTPS, and set
`SOULBRIDGE_SECURE_COOKIES=true` once all access is via that proxy.

## Configuration

Everything is editable in the web UI. Environment variables (prefixed `SOULBRIDGE_`) seed the
defaults on first run; `TZ` and `SOULBRIDGE_SECURE_COOKIES` are read directly.

| Setting | Default | Notes |
|---|---|---|
| `slskd_url` / `slskd_api_key` | `http://slskd:5030` | Your slskd instance |
| `slskd_downloads_path` | `/data/soulseek/complete` | Where finished downloads appear |
| `library_path` | `/data/media/audiobooks` | Where organised books land |
| `folder_template` | `{author}/{title}` | `{author} {title} {narrator}` placeholders |
| `auto_download` | `true` | Auto-grab approved/trusted requests |
| `default_request_mode` | `auto` | `auto` or `interactive` (per-request selectable) |
| `preferred_formats` | `m4b,m4a,mp3,flac,ogg` | Priority order |
| `min_size_mb` / `max_size_mb` | `50` / `4000` | Sanity bounds |
| `write_metadata` | `true` | Tag files from the Audible listing after download |
| `audible_region` | `us` | Catalog and product-link region (`us`, `uk`, `au`, `de`, ...) |
| `default_role` | `standard` | Role for new users (`standard` or `trusted`) |
| `request_quota` | `0` | Max open requests per non-admin (0 = unlimited) |
| `plex_login_enabled` | `false` | Enable Sign in with Plex (needs Plex URL + token) |
| `public_url` | — | External HTTPS base, for the Plex sign-in redirect |
| `apprise_urls` | — | One notification URL per line |
| `abr_url` / `abr_api_key` | `http://audiobookrequest:8000` | Optional: request ingestion |
| `abs_url` / `abs_api_key` / `abs_library_id` | — | Optional: library browser + scans |
| `plex_url` / `plex_token` / `plex_library_section_id` | — | Optional: targeted folder scan |
| `jellyfin_url` / `jellyfin_api_key` | — | Optional: library scan |
| `TZ` (env) | `UTC` | Timezone for all displayed timestamps, e.g. `Australia/Brisbane` |

## Acknowledgements

Built on [slskd](https://github.com/slskd/slskd), with metadata from [Audnexus](https://audnex.us)
and the Audible catalog. Inspired by [Soularr](https://github.com/mrusse/soularr),
[Overseerr](https://overseerr.dev/), and [AudioBookRequest](https://github.com/markbeep/AudioBookRequest).

## Disclaimer

Soulbridge automates your own Soulseek client. You're responsible for complying with copyright law
and the Soulseek terms of service where you live. Use it for content you're entitled to.

## License

MIT, see [LICENSE](LICENSE).
