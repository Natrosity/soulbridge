<p align="center">
  <img src="assets/logo.png" alt="Soulbridge" width="160">
</p>

<h1 align="center">Soulbridge</h1>

<p align="center">Request an audiobook, get it from Soulseek, filed and tagged for your library.</p>

---

Soulbridge connects a request source — [AudioBookRequest](https://github.com/markbeep/AudioBookRequest)
or the *arr stack — to the [Soulseek](https://www.slsknet.org/) network via
[slskd](https://github.com/slskd/slskd). Someone requests a book, Soulbridge finds the best copy
on Soulseek, downloads it, organises it into `Author/Title`, and (optionally) tags it from the
Audible listing so it lands clean in **Plex**, **Jellyfin**, and **Audiobookshelf**.

It exists because audiobook coverage on torrent and Usenet indexers is thin, while Soulseek is
full of them — and nothing tied a request workflow to it.

## How it works

```
AudioBookRequest ─►  Soulbridge  ─►  slskd (Soulseek)
  (request board)   search · rank        │  download
                    · tag · organise      ▼
                    /Author/Title/  ◄─ move into library ─►  Plex · Jellyfin · Audiobookshelf
```

1. Someone requests a book in AudioBookRequest (or you search in Soulbridge directly).
2. Soulbridge builds a clean Soulseek query, ranks the responses (format, size, free slot,
   title/author match, unabridged), and downloads the best one through slskd.
3. When the download finishes, Soulbridge moves it into `Author/Title`, tags the files from the
   Audible metadata, and tells your media servers to scan the new folder.

## Features

- **Smart Soulseek matching** — title-first queries, summary/sample filtering, format and size
  preferences, a bias toward single-file M4Bs, and free-slot awareness. Generic one-word titles
  require the author to match so "Fire" doesn't pull a music track.
- **AudioBookRequest integration** — polls approved requests and marks them done when they land.
- **Self-organising** — files into `{author}/{title}` (configurable). No Readarr required.
- **Audible tagging** — rewrites tags and cover art from the Audible listing (via Audnexus),
  filling gaps and only replacing existing tags when the new value is genuinely better.
- **Targeted media-server scans** — Plex scans just the new folder, not the whole library;
  Jellyfin and Audiobookshelf too, with live connection indicators for each.
- **Library browser** — an optional grid of what you already own, pulled from Audiobookshelf
  (Plex and Jellyfin later). Covers are proxied so no token leaves the server.
- **Web UI** — live dashboard, manual search and grab, tag-write history, and settings. No config
  files needed (environment variables seed the defaults if you want them).
- **Light** — FastAPI and SQLite in a single container.

## Quick start

```bash
docker run -d --name soulbridge \
  -p 8793:8793 \
  -v $PWD/config:/config \
  -v /path/to/data:/data \
  ghcr.io/natrosity/soulbridge:latest
```

Open `http://localhost:8793`, go to **Settings**, and point it at slskd (and optionally
AudioBookRequest, Plex, Jellyfin, Audiobookshelf). See
[`docker-compose.example.yml`](docker-compose.example.yml) for a fuller setup.

> Mount the same parent directory that holds both your slskd downloads and your audiobook library
> (e.g. `/data`), so finished files move into place on one filesystem.

## Configuration

Everything is editable in the web UI. Environment variables (prefixed `SOULBRIDGE_`) seed the
defaults on first run.

| Setting | Default | Notes |
|---|---|---|
| `slskd_url` / `slskd_api_key` | `http://slskd:5030` | Your slskd instance |
| `slskd_downloads_path` | `/data/soulseek/complete` | Where finished downloads appear |
| `abr_url` / `abr_api_key` | `http://audiobookrequest:8000` | Optional; enables request polling |
| `library_path` | `/data/media/audiobooks` | Where organised books land |
| `folder_template` | `{author}/{title}` | `{author} {title} {narrator}` placeholders |
| `auto_download` | `true` | Auto-grab approved requests |
| `preferred_formats` | `m4b,m4a,mp3,flac,ogg` | Priority order |
| `min_size_mb` / `max_size_mb` | `50` / `4000` | Sanity bounds |
| `write_metadata` | `true` | Tag files from the Audible listing after download |
| `plex_url` / `plex_token` / `plex_library_section_id` | — | Optional: targeted folder scan |
| `jellyfin_url` / `jellyfin_api_key` | — | Optional: library scan |

## Acknowledgements

Built on [slskd](https://github.com/slskd/slskd), with metadata from
[Audnexus](https://audnex.us). Inspired by [Soularr](https://github.com/mrusse/soularr) and
[AudioBookRequest](https://github.com/markbeep/AudioBookRequest).

## Disclaimer

Soulbridge automates your own Soulseek client. You're responsible for complying with copyright law
and the Soulseek terms of service where you live. Use it for content you're entitled to.

## License

MIT — see [LICENSE](LICENSE).
