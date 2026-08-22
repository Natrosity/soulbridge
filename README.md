# Soulbridge

**Request audiobooks and fulfil them from Soulseek.** Soulbridge is the missing
link between a request board (like [AudioBookRequest](https://github.com/markbeep/AudioBookRequest))
or the *arr stack and the [Soulseek](https://www.slsknet.org/) network — the place
where audiobooks are actually plentiful. It searches Soulseek via
[slskd](https://github.com/slskd/slskd), picks the best match, downloads it, and
files it neatly into your library for **Audiobookshelf** and **Plex**.

Think of it as *Soularr, but for audiobooks* — a tool that didn't exist until now.

---

## Why

Readarr is retired, and its successors (Chaptarr, Listenarr) rely on Torznab/Usenet
indexers — which have **thin audiobook coverage**. In testing, a popular memoir returned
*zero* audiobook results across a full indexer set, while the same title on Soulseek
returned **100+ sources**, including clean single-file M4Bs. Soulseek is where audiobooks
live, but nothing connected a request workflow to it. Soulbridge does.

## How it works

```
AudioBookRequest ─►  Soulbridge  ─►  slskd (Soulseek)
  (request board)   search · rank        │  download
                    · organise           ▼
                    /Author/Title/  ◄─ move into library ─►  Audiobookshelf + Plex
```

1. A user requests an audiobook in AudioBookRequest (or you search in Soulbridge directly).
2. Soulbridge builds smart Soulseek queries, ranks the responses (format, size, free slot,
   title/author match, unabridged), and downloads the best group via slskd.
3. When the download completes, it's moved into `Library/Author/Title/` and — optionally —
   Audiobookshelf is told to scan. AudioBookRequest is marked fulfilled.

## Features

- 🔎 **Smart Soulseek matching** — title-first queries, spam/summary filtering,
  format & size preferences, single-file-M4B bias, free-slot awareness.
- 🤝 **AudioBookRequest integration** — polls approved requests, marks them downloaded.
- 🗂 **Self-organising** — files into `{author}/{title}` (configurable); no Readarr needed.
- 🖥 **Web UI** — dashboard, manual search & grab, live activity log, settings — no config
  files required (though env-var seeding is supported).
- 📚 **Audiobookshelf-aware** — optional post-import library scan.
- 🪶 **Lightweight** — FastAPI + SQLite, single container.

## Quick start

```bash
docker run -d --name soulbridge \
  -p 8793:8793 \
  -v $PWD/config:/config \
  -v /path/to/data:/data \
  ghcr.io/natrosity/soulbridge:latest
```

Then open `http://localhost:8793`, go to **Settings**, and point it at your slskd
(and optionally AudioBookRequest + Audiobookshelf). See
[`docker-compose.example.yml`](docker-compose.example.yml) for a fuller example.

> **Important:** mount the *same* parent directory that holds both your slskd downloads
> and your audiobook library (e.g. `/data`), so completed files can be moved into the
> library on a single filesystem.

## Configuration

Everything is editable in the web UI; env vars (prefix `SOULBRIDGE_`) seed the defaults on
first run.

| Setting | Default | Notes |
|---|---|---|
| `slskd_url` | `http://slskd:5030` | Your slskd instance |
| `slskd_api_key` | — | slskd API key |
| `slskd_downloads_path` | `/data/soulseek/complete` | Where completed downloads appear |
| `abr_url` / `abr_api_key` | `http://audiobookrequest:8000` | Optional; enables request polling |
| `library_path` | `/data/media/audiobooks` | Where organised books land |
| `folder_template` | `{author}/{title}` | `{author} {title} {narrator}` placeholders |
| `auto_download` | `true` | Auto-grab approved requests |
| `preferred_formats` | `m4b,m4a,mp3,flac,ogg` | Priority order |
| `min_size_mb` / `max_size_mb` | `20` / `4000` | Sanity bounds |

## Status

**v0.1 — early but working.** Roadmap: per-user request approval, narrator-aware matching,
Prowlarr fallback, notifications (Apprise), and a richer transfer view.

## Acknowledgements

Built on the excellent [slskd](https://github.com/slskd/slskd) and inspired by
[Soularr](https://github.com/mrusse/soularr) and [AudioBookRequest](https://github.com/markbeep/AudioBookRequest).

## Disclaimer

Soulbridge is a tool for automating your own Soulseek client. You are responsible for
complying with copyright law and the Soulseek terms of service in your jurisdiction. Use it
for content you are entitled to obtain.

## License

MIT — see [LICENSE](LICENSE).
