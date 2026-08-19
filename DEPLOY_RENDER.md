# Production Deployment (Render)

ClipFarm's production stack is defined as code in [`render.yaml`](./render.yaml): a
Next.js **web** service, a FastAPI **api** service, a Celery **worker**, and a
managed **Redis**. Supabase (Postgres + Auth), Cloudflare R2 (storage) and Modal
(GPU) remain external managed services.

This file covers the steps a person must do — the Blueprint handles everything
else. Tracked as **CF-68**; related: CF-18 (Supabase Pro), CF-89 (monitoring),
CF-90 (secret management), CF-17 (custom SMTP).

> **Two deploy docs exist — this one is the production path.**
> [`DEPLOY.md`](./DEPLOY.md) (CF-41) stands the **backend** up on a self-managed
> VPS with `docker compose`. It is an **alternative self-hosted option**, not a
> stepping stone to this file and not a staging environment — nothing in it is
> reused here. Keep it as a Render escape hatch and a cheap box for long-running
> batch work (reprocessing, the CF-55 eval harness); treat this file as
> authoritative for anything user-facing.
>
> **Why Render is production:** the VPS path keeps secrets as a plaintext
> `.env.docker` on disk, which is exactly what CF-90 (a launch blocker) exists to
> eliminate; it has no managed TLS, is a single point of failure, and runs
> `alembic upgrade head` on **every boot** — the hazard this Blueprint removes by
> moving migrations to `preDeployCommand`.

---

## One-time setup

### 1. Supabase tier (CF-18)
The free tier auto-pauses after ~7 days idle, which takes the whole app down.
**CF-18 (#78)** mitigates this on the free tier with a scheduled keepalive query,
so Pro is **not strictly required just to avoid the pause** once CF-18 is merged
and its first run is confirmed.

> ⚠️ **Sequencing:** #78 is still open, and the keepalive workflow only fires
> from `main`. Until it merges *and* one manual run is confirmed green, the
> free-tier pause is **unmitigated** — so either merge #78 before the first
> apply, or start on Pro.

Pro (~$25/mo) is still recommended before real users for reasons the keepalive
doesn't cover — **automated daily backups** (free tier has none — the biggest
gap for real user data), higher connection limits, and more compute headroom.
Reasonable path: launch a beta on **free + CF-18 keepalive**, upgrade to Pro when
you have real users or the no-backups risk becomes unacceptable.

### 2. Create the Blueprint
1. Render Dashboard → **New → Blueprint**.
2. Connect the **ClipFarmVB/ClipFarm** repo, branch **`main`**.
3. Render reads `render.yaml` and shows the four services + the `clipfarm-shared`
   env group. Apply.

### 3. Fill the secrets
Every env var marked `sync: false` must be pasted in the dashboard. Sources:

**`clipfarm-shared` group** (used by api + worker):

| Key | Where to find it |
|---|---|
| `DATABASE_URL` | Supabase → Project Settings → Database → **Connection pooler** URI. Format: `postgresql+asyncpg://…`. See pooler note below. |
| `SUPABASE_URL` | Supabase → Project Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | same page — **server-only**, never expose |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Cloudflare → R2 → Manage API Tokens |
| `R2_PUBLIC_URL` | your bucket's public URL (`https://<bucket>.r2.dev` or R2 custom domain) |
| `SENTRY_DSN` | Sentry → **clipfarm-api** project → Settings → Client Keys (DSN). Shared by api + worker (worker events are tagged `service:worker`). Blank = monitoring off. |

> **The R2 bucket also needs a CORS policy** — see `infra/README.md`. It is not
> an env var and not code, so nothing above configures it: uploads go browser →
> R2 directly, and without it they fail in the browser however the services are
> set up. The policy's `origins` list must *contain* the web origin you set as
> `CORS_ORIGINS` below, alongside the localhost entries kept for development.
> `https://clipfarm.ca` is already there. **`www` is not, by design** — it is
> redirected to the apex at the edge (step 4) rather than served, so it never
> becomes an origin. Deploying on any other origin, or serving `www` for real,
> means adding it to `infra/r2-cors.json` and re-applying.

**`clipfarm-api`:**
- `API_BASE_URL` → this service's public URL (set after step 4, or its custom domain)
- `CORS_ORIGINS` → the web origin(s), comma-separated, e.g. `https://clipfarm.ca`

**`clipfarm-worker`:**
- `ROBOFLOW_API_KEY` → Roboflow → Settings → API Keys
- `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` → modal.com → Settings → API Tokens (leave blank to run inference locally on CPU — slow)

**`clipfarm-web`:**
- `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` → Supabase API page (anon key is browser-safe)
- `NEXT_PUBLIC_API_URL` → the API's public URL, e.g. `https://api.clipfarm.ca`
- `NEXT_PUBLIC_SENTRY_DSN` → Sentry → **clipfarm-web** project → Client Keys (a different DSN from the api one)
- `SENTRY_ORG`, `SENTRY_PROJECT`, `SENTRY_AUTH_TOKEN` → optional but recommended: enables source-map upload so prod web stack traces show real source lines instead of minified bundles. Token from Sentry → Settings → Auth Tokens.

> `SENTRY_ENVIRONMENT` / `NEXT_PUBLIC_SENTRY_ENVIRONMENT` are already pinned to
> `production` in `render.yaml`, and the release is set automatically from
> `$RENDER_GIT_COMMIT` — no action needed for either.

> **Ordering:** `NEXT_PUBLIC_API_URL`, `CORS_ORIGINS`, and `API_BASE_URL` reference
> URLs that don't exist until services are created. Easiest path: add the custom
> domains (step 4) first so you know the final URLs, then fill these three, then
> trigger a redeploy of web (its values are baked in at build time).

### 4. Domains + HTTPS
- Add a custom domain to **clipfarm-web** (e.g. `clipfarm.ca`) and to
  **clipfarm-api** (e.g. `api.clipfarm.ca`) in each service's Settings.
- Point the DNS records at Render (via Cloudflare). Render provisions HTTPS
  automatically.
- **Redirect `www` to the apex in Cloudflare** (Rules → Redirect Rules,
  `www.clipfarm.ca/*` → `https://clipfarm.ca/$1`, 301). Only the apex is a
  configured origin — in Render, in `CORS_ORIGINS`, in Supabase's Site URL, and
  in the R2 CORS policy. Redirecting at the edge means `www` never becomes an
  origin any of them has to know about; serving it for real would mean adding it
  to all four.
- **Confirm the web domain is in the R2 CORS policy.** Uploads go browser → R2
  directly (CF-163), so an origin the bucket does not allow fails at preflight
  however the services are configured.

  ```bash
  npx wrangler@4 r2 bucket cors list clipfarm
  ```

  `https://clipfarm.ca` is applied, so if you are serving the custom domain this
  is a check rather than a step.

  > ⚠️ **If you deploy before the custom domain is live, the origin is the
  > `onrender.com` URL shown on the clipfarm-web service page — whatever Render
  > assigned, since it appends a suffix when the service name isn't globally
  > free — and it is not in the policy.** That is
  > the normal first deploy, not an edge case — every upload will fail at
  > preflight until the origin is added. The same applies to any other host.

  To add one, edit `origins` in `infra/r2-cors.json` and re-apply:

  ```bash
  npx wrangler@4 r2 bucket cors set clipfarm --file infra/r2-cors.json
  ```

  Edit the file rather than issuing a second command: `cors set` replaces the
  policy instead of merging, so a second invocation drops what the first one
  set. See `infra/README.md`.
- Supabase → Authentication → **URL Configuration**: set Site URL to the web
  domain and add `https://<web-domain>/auth/callback` to **Redirect URLs**.
  Google sign-in lands there; a link back to any other path is rejected by
  Supabase and the user gets "requested path is invalid".
- Supabase → Authentication → **Email Templates** → *Confirm signup*: replace the
  default `{{ .ConfirmationURL }}` link with

  ```html
  <a href="{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=signup">Confirm your email</a>
  ```

  **This is required, not optional.** The default template sends a PKCE link that
  only works in the browser that started signup — sign up on a laptop, tap the
  link on a phone, and it fails with `bad_code_verifier`. `/auth/confirm` verifies
  the token server-side, so any browser works. Until the template is changed the
  route is never reached and nothing improves (CF-16).

  The link is built from **Site URL**, so it must point at the web domain — a
  preview or staging deploy on another host needs its own Supabase project.

  Keep `type=signup` exactly as written. `/auth/confirm` only accepts the types
  it has a destination for and rejects the rest, so Supabase's generic
  `type=email` example bounces to the login page instead of confirming. Wiring
  another template (password reset, email change) means adding its type to the
  route *and* deciding where that link should land — reset in particular must
  not drop the user on `/games` still needing a new password.

### 5. Custom SMTP for auth emails (CF-17)

> ⚠️ **Unlike every other step in this file, this one has not been executed
> against the live project.** It is written from provider and Supabase docs, not
> from a run. Treat the field values as *expected* and verify each against what
> the dashboard actually shows — Supabase in particular moves auth settings
> between pages between releases, so a nav path that doesn't match is far more
> likely to be this doc being stale than you being in the wrong project. Correct
> this section as you go and drop this banner once it has been walked once.

**Supabase's built-in sender is not a production mailer.** It is rate-limited to
a couple of messages per hour per project (Supabase has lowered this number
before — read the current value off Authentication → Rate Limits rather than
trusting this line), it sends from a shared Supabase domain nobody can
authenticate as us, and it is documented as being for development only.

Two distinct failure modes follow, and they are worth keeping apart because only
one of them is silent:

- **Over the per-hour cap** the signup call itself fails —
  `over_email_send_rate_limit`, HTTP 429. `web/src/app/signup/page.tsx` renders
  `error.message` in the red alert box, so the user is told and the cause is
  named. Loud, and already surfaced.
- **Under the cap but the send fails afterwards** (bad credentials, unverified
  sender, provider outage) Supabase accepts the signup and the delivery fails
  behind it. Nothing propagates back: the user sits on "Check your inbox"
  forever. **This is the case custom SMTP and the verification below exist for.**

Do this **after** step 4 — the sending domain and the Site URL both depend on
the real domain existing.

**Provider: Resend.** 3k emails/month free, SMTP endpoint, per-message logs.
Nothing below is Resend-specific except the hostname and the username: any
provider that exposes SMTP works the same way, and swapping means changing the
five fields in the Supabase form.

Substeps are lettered so that a reference to one can't be mistaken for a
reference to a numbered **step** of this file.

- **(a) Resend → Domains → Add Domain.** Use a *subdomain*, e.g.
  `mail.clipfarm.ca`, not the apex. Auth mail then can't damage the apex
  domain's reputation, and it keeps any future marketing sender independent.

- **(b) Add the DNS records it shows** in Cloudflare, on the subdomain — DKIM and
  SPF as `TXT`, the return-path `MX`, and for some DKIM configurations a
  `CNAME`. **Any `CNAME` must be set to "DNS only"** (grey cloud); proxying it
  breaks verification. `MX` and `TXT` records have no proxy toggle at all, so
  don't go hunting for one there. Verification usually lands in minutes.

- **(c) Add a DMARC record for the sending subdomain:**
  `_dmarc.mail.clipfarm.ca  TXT  "v=DMARC1; p=none; rua=mailto:<a real mailbox>"`.

  Publish it on the **subdomain**, not just the apex. DMARC falls back to the
  organizational domain when a subdomain has no record of its own, so an apex
  policy with no `sp=` governs auth mail too — and tightening the apex later for
  a marketing sender would silently tighten confirmation mail with it, which is
  exactly the isolation substep (a) was chosen to buy. (Publishing `sp=none` on
  the apex instead works, but the subdomain record is harder to undo by
  accident.)

  `rua=` is a **placeholder — put an address that actually receives mail there.**
  Reports sent to a non-existent mailbox are dropped, and then the "tighten once
  the reports are clean" condition below can never be evaluated. Start at
  `p=none` and only move to `quarantine` on clean reports; going straight to a
  strict policy is a good way to have your own confirmation mail rejected.

- **(d) Resend → API Keys → Create**, permission **Sending access**. Copy it
  once; it isn't shown again.

- **(e) Supabase → Authentication → Emails → SMTP Settings → Enable Custom SMTP**
  (older dashboards file this under Project Settings → Auth):

  | Field | Value |
  |---|---|
  | Host | `smtp.resend.com` |
  | Port | Start with `465` (implicit TLS); if the connection fails, switch to `587` (STARTTLS). Resend supports both, and which one a given Supabase project prefers hasn't been confirmed against the live dashboard. `25` is blocked everywhere and will just time out. |
  | Username | `resend` — literally that string, not an email address |
  | Password | the API key from substep (d) |
  | Sender email | `noreply@mail.clipfarm.ca` — must be on the **verified** domain, or every send is rejected |
  | Sender name | `ClipFarm` |

- **(f) Supabase → Authentication → Rate Limits → "Emails sent per hour".** Custom
  SMTP does not raise this by itself — the ceiling stays where it was until you
  raise it. Set it to something that covers a launch-day burst (e.g. 100/hr) and
  keep it *below* the provider's own limit. That way the ceiling you hit first is
  Supabase's, which fails loudly as `over_email_send_rate_limit` on the signup
  call, rather than the provider's, which fails after Supabase has already told
  the user to check their inbox.

**Verify:** sign up with a real address on the live domain and confirm three
things — the mail arrives, **Resend → Logs** shows the send (this is where a
rejected sender or bad key surfaces; Supabase's own UI won't tell you), and it
lands in the inbox rather than spam. Gmail and Outlook are the two worth checking;
they weigh SPF/DKIM alignment differently.

**Notes:**
- **No repo or Render secret is involved.** The API key lives only in Supabase's
  dashboard, which is what CF-90 wants — don't mirror it into `render.yaml` or an
  env group.
- **Rotating the key is a two-step:** create the new key in Resend, paste it into
  Supabase, *then* revoke the old one. Revoking first silently breaks signup.
- **Nothing detects a later silent failure — accepted for now, with one cheap
  mitigation.** The verification above is one-time. A botched rotation, an
  exhausted free tier, or a lapsed domain verification stops delivery while
  `/healthz` stays green and Sentry sees nothing: the failure is inside Supabase's
  mailer, not in any code this repo runs, so the first signal is a user complaint.
  Blind operation is tolerable at current volume, but **turn on Resend's bounce
  and delivery-failure notifications** while you're in the dashboard — it costs a
  checkbox and converts the silent case into an email. Real alerting belongs with
  CF-89 (#107).
- **Only *Confirm signup* is wired today.** That is the one template step 4
  rewrote and the only type `/auth/confirm` accepts, so it is the only auth mail
  that goes out — there is no password-reset flow in the app yet. Any template
  enabled later needs the same `token_hash` rewrite and its type added to the
  route, per the note above.
- **Deliverability degrades if this domain is only used for auth mail in
  bursts.** A domain that sends nothing for weeks and then 200 messages in an hour
  looks like a compromised sender. Not worth engineering around now, but it is the
  likely explanation if mail suddenly starts landing in spam after a quiet period.

---

## Verify the deploy
1. `clipfarm-api` → open `/healthz` (shallow liveness — what Render's health
   check watches), expect `{"status":"ok"}`. Once CF-89 (#107) merges, also point
   an **external uptime monitor** at `/health` — the deep check that returns 503
   when Postgres/Redis is down, so it can alert.
2. Load the web domain, sign up (confirm the verification email arrives — if it
   doesn't, custom SMTP is the first thing to check, see step 5 above).
   **Open that link in a different browser than you signed up in** — that is the
   case the token_hash template exists for, and the one that silently regresses
   if the template is ever reset to the default.
3. Upload a video → confirm the worker picks it up (worker logs) and clips
   appear. This exercises api → Redis → worker → R2 → Modal end to end.

   **Use a file over 100 MiB** (`single_put_max_bytes`), and keep the browser's
   network panel open. Since CF-163 the video goes browser → R2 directly, and
   the two paths fail differently: a smaller file is uploaded with one presigned
   PUT that never reads an ETag, so it passes even when the bucket's CORS policy
   is missing `ExposeHeaders: ETag` and every multipart upload is broken. A short
   clip no longer tests the upload path that matters.

   What to check: several `PUT`s to `*.r2.cloudflarestorage.com`, **no upload
   bytes to the api origin**, then one `POST /games/{id}/uploads/complete`. See
   `infra/README.md` for the bucket settings this depends on.
4. **Model cache disk** — check the worker logs on first job: it should download
   the ball model once, then on a redeploy **not** re-download it. If you see a
   `PermissionError` writing to `/models`, the disk mounted root-owned and the
   non-root worker user can't write it (see the note in `render.yaml`) — fall
   back by pointing the `MODEL_*` vars off the disk, or run the worker as root.
5. Redeploy once and confirm no data loss.

---

## Deploying a change

**Auto-deploy is off on all three services.** Merging to `main` does *not* ship
to production — you deploy deliberately:

1. Merge the batch to `main`.
2. A game mid-processing is no longer a reason to wait — it is requeued and
   re-run after the deploy (see "Deploying kills in-flight jobs" below). It does
   restart from the beginning, so deploying under load still costs the work in
   flight.
3. Render Dashboard → the service → **Manual Deploy → Deploy latest commit**.
4. Deploy **`clipfarm-api` first** (its `preDeployCommand` applies migrations),
   then `clipfarm-worker`, then `clipfarm-web`. The worker shares the api's
   models, so shipping it against an un-migrated schema is the thing this
   ordering avoids.

### Deploying kills in-flight jobs (and they recover)

Every Render deploy hard-kills the running worker. Two changes make that
survivable rather than something to schedule around:

- **CF-65a** — `task_acks_late` + `task_reject_on_worker_lost` mean a killed
  task is requeued instead of lost.
- **CF-184** — the per-game lock is a *session-scoped Postgres advisory lock*,
  held on its own connection. The kill closes that connection, Postgres drops
  the lock, and the requeued copy acquires it and runs.

Until CF-184 the second half was missing: the old Redis lock had a 3h TTL that
outlived the dead worker, so the requeued copy found the lock held, no-opped,
and the game sat in `processing` until someone intervened. That is what #149's
stale-`processing` reaper was for — with the lock released by the kill itself,
there is nothing left to reap.

What a deploy still costs is **the work in flight**: a requeued game restarts
from the beginning, not from where it was killed. So deploying while a long
match is processing is wasteful, not dangerous.

> ⚠️ **This depends on `DATABASE_URL` (or `LOCK_DATABASE_URL`) being a
> session-mode connection.** Supabase's transaction-mode pooler (port **6543**)
> can serve consecutive statements from different backends, which cannot hold a
> session-scoped lock. The worker checks after acquiring and **refuses to
> process** rather than run under a lock that does not hold — if worker logs
> show `LockNotSessionScoped`, set `LOCK_DATABASE_URL` to the session-mode
> connection (port **5432**) in the `clipfarm-shared` env group.

**Why auto-deploy is off:** the api runs `alembic upgrade head` on every deploy. With
auto-deploy on, any merge to `main` would apply a migration to the production
database unattended — the same class of failure as the 007→008 crash-loop, one
environment over, and at odds with `CONTRIBUTING.md` treating migrations as a
coordinated step.

**This is deliberately the substitute for a staging environment.** There isn't
one yet, and manual deploys buy the same protection for migrations at zero cost:
a human is the gate. Note the kanban's "Staging" column is a *workflow state*
("merged, awaiting deploy"), not an environment — it keeps working as-is. A real
staging environment is **CF-152**, to be added as a second service group in this
same Blueprint when there are real users; that's also what would let
`autoDeploy` come back on (`clipfarm-web` first, it's the safest).

---

## Notes & gotchas

- **Start commands live in `scripts/render-*.sh`, not inline in `render.yaml`.**
  These fields are not tokenized the way a shell would tokenize them: a quoted
  body arrives as a single **word**. So `sh -c "VAR=x cmd args"` starts a shell
  that then hunts for one command named by the entire string, and the service
  dies at boot with

  ```
  sh: 1: SENTRY_RELEASE=<sha> celery -A app.workers.celery_app worker …: not found
  ```

  That reads like a missing binary and isn't — celery and uvicorn are both in the
  image. (The `sh: 1:` prefix is dash's own error format, so a shell *did* run;
  what failed was the splitting, not the availability of a shell.) It cost a full
  blueprint deploy to diagnose (CF-170).

  This applies to **`preDeployCommand` as well as `dockerCommand`** — the
  migration hook runs before the service starts, so the same shape there aborts
  the deploy before any start script is reached. Anything needing a shell
  (`$PORT`, `$RENDER_GIT_COMMIT`, a `cd`) belongs in a script; if you inline it
  again, the backend stops deploying.
- **Migrations run once per deploy** via the api service's `preDeployCommand`
  (`alembic upgrade head`), not on every boot. Don't re-add per-boot migration to
  production start commands — it races across restarts and can advance a shared
  schema unexpectedly.
- **Supabase pooler + asyncpg:** the transaction-mode pooler doesn't support
  prepared statements. If you hit `prepared statement already exists`, use the
  session-mode pooler port or append the appropriate asyncpg options. (The dev
  stack already runs against the pooler, so the working URL format carries over.)
- **Worker sizing** is the main cost lever. It ships on `standard` (2 GB) with
  the **light** `POSE_*` values (`yolov8n-pose` @ 640) — the config the dev stack
  has actually exercised. `app/config.py`'s defaults (`yolov8s-pose` @ 1280) are
  higher quality but unproven on a 2 GB box; raise the env values once you've
  measured real headroom, rather than discovering an OOM on day one. Better
  long-term: offload more of the pipeline to Modal (CF-65) so the box stays small.
- ⚠️ **Production runs the light pose config, so its action labels are weaker
  than the `config.py` defaults.** Clip *boundaries* come from ball tracking and
  are unaffected, but per-clip action labels (dig/set/spike — the subject of
  CF-3) will be worse than a run using the defaults. **Don't compare production
  label quality against CF-55's eval baselines** unless the eval was run with the
  same `POSE_*` values — they'd disagree for configuration reasons, not model
  ones. Raise the prod values to match before drawing any conclusion.
- **Region:** everything is pinned to `oregon`. Confirm the Supabase project sits
  in (or nearest to) that region — every DB round-trip pays the difference, and
  CF-47's progress writes made the worker chattier, so a cross-region mismatch
  would show up there first.
- **Broker and result backend share one Key Value instance in prod.** The dev
  compose splits them by database index (`/0` broker, `/1` results); Render hands
  out a single `connectionString`, so both land in the same instance. Functionally
  fine (Celery namespaces its keys), but result metadata then accumulates
  alongside the queue — and `noeviction` is set precisely so a *full* instance
  errors loudly rather than dropping jobs. Nothing in `api/` ever reads a result
  (no `AsyncResult`/`.get()`; progress is polled from Postgres), so those writes
  are pure overhead. Tracked separately — see the `task_ignore_result` follow-up.
- **Queued jobs survive a broker restart — but only on a paid plan.** Free Key
  Value instances have **no persistence**, so a restart would silently drop every
  queued game — and since CF-65a landed, in-flight ones too (`acks_late` keeps a
  task on the broker until it finishes, so an unpersisted restart drops it). Paid
  plans persist by default (journal + snapshot, ~1s of writes at risk). This is
  why `render.yaml` pins `plan: starter` and not `free`. Note that *upgrading*
  the instance type requires a restart and can itself lose data depending on the
  persistence mode — drain the queue first.
- **Redis is the broker** — `maxmemoryPolicy: noeviction` is set so a full
  instance surfaces an error rather than silently dropping queued jobs.

## Security (CF-90)
- No production secret should live in a file in this repo — everything is in
  Render's secret store or the `clipfarm-shared` group.
- **Rotate any credential that has ever been shared in plaintext** (chat, tickets,
  screenshots) before go-live — notably the Modal tokens and the Supabase DB
  password. Set the fresh values in Render, not in `.env` files.
