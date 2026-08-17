# Bucket configuration

Settings that live on the Cloudflare R2 bucket rather than in application code.
They are checked in because nothing in the app can enforce them and nothing
reconciles them against the dashboard — a wrong value is invisible until an
upload fails in a browser.

Applied with `wrangler` (`npx wrangler login`, or a scoped
`CLOUDFLARE_API_TOKEN` with **Workers R2 Storage: Edit**), or by hand in the
Cloudflare dashboard under **R2 → your bucket → Settings**.

> Commands here pin `wrangler@4`. The flags below were validated against
> 4.120.1; an unpinned `npx wrangler` may resolve to a major version whose
> interface differs.

---

## CORS policy

Required by CF-163: the browser PUTs video straight to R2, so the bucket has to
accept cross-origin writes from the web app. Without this, uploads fail at the
first request.

`r2-cors.json` is the policy as applied. It is tracked, not gitignored — the
point of this directory is that a change to the bucket's config gets reviewed
like any other change, and the file holds only web origins, which are already
public in `DEPLOY_RENDER.md` and `api/app/config.py`. Nothing secret goes here.

> **Provenance:** applied to the `clipfarm` bucket and verified field-for-field
> against `cors bucket cors list` on **2026-08-17**. The CORS policy was also
> exercised end to end — a browser at an allowed origin PUT two presigned
> multipart parts and read both ETags back, which is the behaviour
> `exposeHeaders` exists for. If you change this file, re-apply it and update
> this line; a record nobody reconciles is the problem this directory exists to
> fix.

```bash
npx wrangler@4 r2 bucket cors set clipfarm --file infra/r2-cors.json
```

> **This is the R2 API's schema** — a `rules` array of `{allowed: {origins,
> methods, headers}, exposeHeaders, maxAgeSeconds}`. It is *not* the S3-style
> `[{"AllowedOrigins": …}]` JSON that most CORS documentation shows. Handing
> wrangler the S3 shape fails with *"The CORS configuration file must contain a
> 'rules' array as expected by the R2 API"* — the field names and the nesting
> both differ, so the two are not interchangeable.

### Origins

> ⚠️ **As committed, `r2-cors.json` contains development origins only** —
> `localhost:3000` and `127.0.0.1:3000`. There is no deployed web origin yet, and
> this file records what is actually on the bucket rather than an aspiration.
>
> **Before the first production deploy, add the web origin to this file and
> re-apply it.** Skipping that leaves the bucket rejecting the live domain and
> every upload fails at preflight. Because `cors set` replaces rather than
> merges, applying this file *as it stands* to a bucket that already has a
> production origin would remove it — so edit the file, don't run a second
> command.

One bucket serves every environment, so `origins` is intended as a **superset**:
production plus the localhost entries used in development. It is therefore never
the same value as the api's `CORS_ORIGINS` — that is a comma-separated string for
one deployment, this is a JSON array covering all of them. The requirement is
that this list *contains* whatever origin the browser is actually on.

R2 string-matches without normalising, so a trailing slash, a missing scheme, or
`http` where the site serves `https` all fail silently.

`cors set` **replaces** the whole policy rather than merging into it, so the file
must always list every origin you want — adding production means editing this
file and re-applying, not issuing a second command.

### What each field is for

| Field | Why |
|---|---|
| `PUT` in `methods` | The upload itself. Without it, no upload can start. |
| `content-type` in `headers` | The single-PUT path sends `Content-Type` because the api signs it into the URL, and a signed header must be allowed through preflight. Part PUTs send no headers — `file.slice()` yields an untyped `Blob`. |
| `ETag` in `exposeHeaders` | **The easy one to miss.** Multipart completion sends each part's ETag back to the api, and a cross-origin response header is unreadable to JavaScript unless explicitly exposed. |
| `GET`, `HEAD` in `methods` | Not currently exercised cross-origin — playback uses `<video>` without `crossOrigin`, and the completion `HEAD` is server-side. Kept because they cost nothing and any future browser-side fetch of an object (canvas, thumbnail, range request) would need them. |

On the ETag row: `web/src/lib/upload.ts` guards per part, so a bucket missing
`exposeHeaders` fails after the first few parts with an error that names
`ExposeHeaders` directly, rather than after the whole file has transferred. That
guard is why the symptom is survivable — it is not a reason the field is
optional.

### Verifying

```bash
npx wrangler@4 r2 bucket cors list clipfarm
```

That prints the live policy, which is the only cheap way to confirm `ETag` is
actually exposed. A preflight `curl` cannot tell you: browsers send
`Access-Control-Expose-Headers` on real responses, never on the `OPTIONS`
preflight, so a preflight check passes with or without it.

R2 ships **no** default CORS policy — a bucket that has never been configured
prints nothing here. An unexpected policy means someone set one previously.

The end-to-end check is **an upload larger than `single_put_max_bytes`**
(default 100 MiB). Anything smaller takes the single-PUT path, which never reads
an ETag — a small test upload passes green while multipart is still broken.

---

## Lifecycle rule — abort incomplete multipart uploads

The parts of an unfinished multipart upload are billed until the upload is
aborted, and they do not appear in a normal object listing.

The api already aborts on two paths — deleting a game, and the
abandoned-upload sweep in `create_upload` — so this is a backstop for uploads
neither reaches, such as a process dying between starting the upload and
recording its row.

**R2 creates this rule by default**, so most buckets already have it. Check
before adding anything:

```bash
npx wrangler@4 r2 bucket lifecycle list clipfarm
```

A bucket with `Default Multipart Abort Rule` — *abort incomplete multipart
uploads after 7 days*, all prefixes, enabled — needs no action. If it is missing
or disabled:

```bash
npx wrangler@4 r2 bucket lifecycle add clipfarm abort-incomplete-uploads \
  --abort-multipart-days 7
```

Seven days is comfortably longer than `upload_url_ttl_seconds` (6h), so it can
never abort an upload a client could still legitimately finish.
