# Bucket configuration

Settings that live on the Cloudflare R2 bucket rather than in application code.
They are checked in because nothing in the app can enforce them and nothing
reconciles them against the dashboard — a wrong value is invisible until an
upload fails in a browser.

Both are applied with `wrangler` (`npx wrangler login` first, then the commands
below), or by hand in the Cloudflare dashboard under **R2 → your bucket →
Settings**.

---

## CORS policy

Required by CF-163: the browser PUTs video straight to R2, so the bucket has to
accept cross-origin writes from the web app. Without this, every upload fails
at the first request.

`r2-cors.example.json` is the shape. Copy it, set `AllowedOrigins`, apply:

```bash
cp infra/r2-cors.example.json infra/r2-cors.json   # gitignored
npx wrangler r2 bucket cors set clipfarm --file infra/r2-cors.json
```

`AllowedOrigins` is the one field that differs per environment, which is why
this is an `.example` file rather than the real thing — same convention as
`.env.docker.example`.

**Use the same value as the api's `CORS_ORIGINS`.** Both describe the browser's
origin, and if they disagree one of the two layers breaks. R2 string-matches
without normalising, so a trailing slash, a missing scheme, or `http` where the
site serves `https` all fail silently.

Every other field is load-bearing:

| Field | Why |
|---|---|
| `PUT` | The upload itself. `GET`/`HEAD` cover playback and the completion check. |
| `content-type` in `AllowedHeaders` | The single-PUT path sends `Content-Type` because the api signs it into the URL. Without this, preflight fails on small uploads. |
| `ETag` in `ExposeHeaders` | **The easy one to miss.** Multipart completion sends each part's ETag back to the api, and a cross-origin response header is unreadable to JavaScript unless it is explicitly exposed. Omit it and multipart uploads fail at assembly, after every byte has already transferred. |
| `http://localhost:3000` | Keeps the Docker dev stack working against the same bucket. Drop it if dev uses its own. |

### Verifying

```bash
npx wrangler r2 bucket cors list clipfarm
```

That prints the live policy, which is the only cheap way to confirm `ETag` is
actually exposed. A preflight `curl` cannot tell you: browsers send
`Access-Control-Expose-Headers` on real responses, never on the `OPTIONS`
preflight, so a preflight check passes with or without it.

The end-to-end check is **an upload larger than `single_put_max_bytes`**
(default 100 MiB). Anything smaller takes the single-PUT path, which never
reads an ETag — a small test upload passes green while multipart is still
broken.

---

## Lifecycle rule — abort incomplete multipart uploads

The parts of an unfinished multipart upload are billed until the upload is
aborted, and they do not appear in a normal object listing.

The api already aborts on two paths — deleting a game, and the
abandoned-upload sweep in `create_upload` — so this is a backstop for uploads
neither reaches, such as a process dying between starting the upload and
recording its row.

```bash
npx wrangler r2 bucket lifecycle add clipfarm abort-incomplete-uploads \
  --abort-multipart-days 7

npx wrangler r2 bucket lifecycle list clipfarm   # verify
```

Seven days is comfortably longer than `upload_url_ttl_seconds` (6h), so it can
never abort an upload a client could still legitimately finish.
