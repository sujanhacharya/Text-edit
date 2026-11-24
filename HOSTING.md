# Hosting InkPatch

## Table of contents

- [1. Short answer](#1-short-answer)
- [2. What is being hosted](#2-what-is-being-hosted)
- [3. Privacy and data-flow boundaries](#3-privacy-and-data-flow-boundaries)
- [4. Free and low-cost hosting options](#4-free-and-low-cost-hosting-options)
- [5. Recommended deployment plans](#5-recommended-deployment-plans)
- [6. Resource sizing and tuning](#6-resource-sizing-and-tuning)
- [7. Oracle Cloud deployment walkthrough](#7-oracle-cloud-deployment-walkthrough)
- [8. Docker and reverse-proxy reference](#8-docker-and-reverse-proxy-reference)
- [9. Storage, backup, restore, and retention](#9-storage-backup-restore-and-retention)
- [10. Authentication, access control, and abuse prevention](#10-authentication-access-control-and-abuse-prevention)
- [11. Monitoring and operations](#11-monitoring-and-operations)
- [12. Optional OCR and AI sidecars](#12-optional-ocr-and-ai-sidecars)
- [13. Hosted deployment checklist](#13-hosted-deployment-checklist)
- [14. Cost control](#14-cost-control)
- [15. Troubleshooting](#15-troubleshooting)
- [16. References](#16-references)
- [17. Maintenance of this document](#17-maintenance-of-this-document)

This is the operational hosting plan for InkPatch. It covers the default
local-first deployment, free and low-cost remote options, privacy boundaries,
authentication, persistent storage, backups, resource sizing, and the optional
OCR/AI sidecars that could be added later.

**Plan checked:** 2026-11-16. Free-tier limits change frequently. Re-check the
provider links at the end of this document before committing to a host.

---

## 1. Short answer

| Goal | Recommended plan | Why |
| --- | --- | --- |
| **Private, no monthly cost** | Run InkPatch on a home PC, mini PC, or Raspberry Pi 4/5 (4 GB+) and reach it through **Tailscale Serve** | Nothing is published to the public internet; Tailscale handles identity and TLS; no port forwarding |
| **Public demo, no monthly cost, best-effort uptime** | **Oracle Cloud Always Free** Ampere A1 VM (2 OCPU / 12 GB) + Docker + Caddy + an outer authentication layer | The only mainstream free VM with enough RAM for OCR; 200 GB storage and generous egress |
| **Public from home, no monthly cost** | Home server + **Cloudflare Tunnel + Cloudflare Access** | Outbound-only tunnel, no open router ports, free Access login in front; Cloudflare can see proxied plaintext |
| **Cheapest reliable paid fallback** | Fly.io shared-cpu-1x 1 GB (about US$6.70/month) or a small Hetzner/OVH VPS | Predictable memory, persistent volume, no free-tier capacity lottery |
| **Not recommended** | Render/Koyeb free web services, Hugging Face free compute Spaces, Vercel/Netlify/Cloudflare Pages, Google Cloud e2-micro as a primary host | 512 MB RAM, sleep/scale-to-zero, ephemeral disks, no Tesseract, or only 1 GB RAM |

Two rules apply to every public deployment:

1. **Put an authenticated TLS proxy or private network in front of the app.**
   InkPatch has per-document capability cookies but no user accounts, no login,
   and no rate limiting. Never expose Uvicorn directly on port 8000.
2. **Set `TEXTEDIT_COOKIE_SECURE=1`** whenever the browser reaches the app over
   HTTPS. Keep the app itself bound to `127.0.0.1` and let Caddy, Nginx,
   Cloudflare Tunnel, or Tailscale terminate TLS.

---

## 2. What is being hosted

InkPatch is a single-process FastAPI application. It serves a static frontend,
runs Tesseract through a subprocess, uses OpenCV/NumPy/Pillow for image
processing, and stores each document as three files:

```text
data/<uuid>/
  image.png       Straightened, size-limited working image
  preview.png     Latest render, when one exists
  doc.json        OCR lines, sampled styles, metadata, access-token hash
```

Important facts for hosting:

- **One Uvicorn worker only.** OCR is serialized in-process and renders are
  locked per document. Do not use `--workers 2+` or scale the same data
  directory across multiple instances.
- **No database and no cloud dependency.** The only runtime external process is
  the local Tesseract binary. Fonts come from bundled assets and the host.
- **No user accounts.** Each upload gets a random 256-bit token in an
  `HttpOnly`, `SameSite=Strict` cookie scoped to `/api/documents/<id>`. Only a
  SHA-256 hash is stored in `doc.json`. There is no document listing and no
  ownership model.
- **Creation endpoints are public by default.** `POST /api/documents` and
  `POST /api/demo` do not require a cookie. On a reachable host, an outer
  authentication layer must cover the whole site, not just `/api/documents/`.
- **Documents expire.** `TEXTEDIT_TTL_HOURS` defaults to 96 hours (four days)
  without a change. Cleanup runs at startup and hourly and deletes the whole
  document folder. Viewing or downloading does not reset the timer.
- **No encryption at rest.** Working images, previews, and `doc.json` are
  ordinary files under `data/` or the `inkpatch-data` volume.
- **No rate limiting, audit log, malware scanning, or CSRF token.** The
  reverse proxy, tunnel, or private network is the outer security boundary.

The default Compose file already publishes only on `127.0.0.1`, drops all
capabilities, sets `no-new-privileges`, runs as a non-root user, and includes a
health check. Keep those defaults.

---

## 3. Privacy and data-flow boundaries

### 3.1 Deployment modes

| Mode | Who can read the image? | Where the image is stored | Network path |
| --- | --- | --- | --- |
| **Local default** (`uvicorn` or Compose on loopback) | You and anyone with access to your machine account | Your disk or local Docker volume | No network call at all |
| **Private remote** (home server + Tailscale Serve, or a VM reachable only through a tailnet) | You, the host operator, and the infrastructure provider | Host disk/volume | Encrypted WireGuard mesh; Tailscale relays cannot decrypt |
| **Operator-hosted public** (Oracle/GCP VM + Caddy, or home + Cloudflare Tunnel) | You, the operator, the hosting provider, and the TLS-terminating proxy | Host disk/volume | TLS to the proxy; proxy to origin may be plain HTTP on loopback |
| **Public with outer auth** (Cloudflare Access, Caddy basic auth, or Tailscale identity) | The same parties as above, limited to authorized users | Host disk/volume | Same; the auth layer decides who reaches the app |
| **Third-party OCR/AI API** (not implemented) | The API provider receives the image or text | Provider's service | Outbound HTTPS; this is a privacy change, not a drop-in optimization |

### 3.2 What the app does not do

By default InkPatch has:

- no telemetry, analytics, crash reporting, or update check;
- no model download at startup;
- no remote font fetch;
- no cloud OCR or LLM call;
- no email, webhook, or external database;
- no CDN or external frontend asset.

Tesseract is a pre-trained LSTM OCR model and is the only AI component. It runs
locally. NumPy and OpenCV are numerical/image-processing libraries, not AI. The
project does not include an LLM, a diffusion model, or a generative image model.

### 3.3 Hosted-operator responsibilities

If you host InkPatch for other people, the "no image leaves the host" statement
is no longer the whole story: uploads leave the user's device and arrive at your
server. Before inviting users:

- Publish a short privacy notice naming the host, retention period, backup
  policy, and contact address.
- Tell users that documents are stored unencrypted on the server and are
  deleted after the configured TTL.
- Do not enable debug logging of cookies, authorization headers, or request
  bodies. The app does not log cookie values, but reverse proxies and tunnels
  can log more than you expect.
- Keep backups encrypted and access-controlled. Backups are copies of user
  images.
- Use a hosting region and provider that match your legal/privacy obligations.
  Oracle/GCP/Cloudflare may process data outside the user's country.
- If you add a cloud OCR/LLM service, disclose it per upload and make it
  opt-in. Do not silently send images to a third party.

---

## 4. Free and low-cost hosting options

### 4.1 Comparison (checked 2026-11-16)

| Provider | Free allowance | RAM / CPU | Persistent storage | Sleep / lifecycle | Fit for InkPatch |
| --- | --- | --- | --- | --- | --- |
| **Oracle Cloud Always Free** | Ampere A1: 1,500 OCPU-hours + 9,000 GB-hours/month (2 OCPU + 12 GB total); 2 AMD micro VMs; 200 GB boot+block storage; 10 TB egress/month | 2 OCPU / 12 GB Arm (or split across two VMs) | 200 GB combined boot/block volume; persistent | Always on, but capacity is not guaranteed and Oracle may reclaim idle resources; home region only | **Best free option for a continuously running VM.** Enough RAM for Tesseract/OpenCV; ARM build works with current wheels |
| **Google Cloud Free Tier** | 1 non-preemptible `e2-micro` in `us-west1`, `us-central1`, or `us-east1`; 30 GB standard disk; 1 GB North America egress/month | 1 GB RAM, shared vCPU | 30 GB persistent disk | Always on while within monthly hours; billing account required | **Tight.** May work for small screenshots with swap and reduced settings; large phone photos can OOM. 1 GB egress is very small |
| **Render Free** | 750 instance-hours/workspace/month; 5 GB bandwidth; 500 build minutes | 512 MB, 0.1 CPU | **No persistent disk on free**; filesystem lost on restart/spin-down | Sleeps after 15 minutes idle; about 1 minute cold start; may restart at any time | **Not suitable.** OCR needs more memory, and documents vanish on spin-down |
| **Koyeb** | Existing Starter organizations keep one free 512 MB instance; new organizations must choose a paid plan (since February 2026) | 512 MB, 0.1 vCPU | No volumes on free | Scales to zero after 1 hour idle | **Not suitable for new deployments** |
| **Fly.io** | No free tier for new organizations; US$1 trial credit for 7 days | Smallest always-on: 256 MB from about US$2.19/30 days; 1 GB about US$6.70 | Volumes available (paid) | Machines can auto-stop, but stopped machines still bill rootfs | **Good cheap paid fallback** with a volume; not free |
| **Hugging Face Spaces** | Static Spaces free; Gradio/Docker compute Spaces require a paid plan (PRO for personal accounts) | CPU Basic: 2 vCPU / 16 GB, **50 GB non-persistent** | Non-persistent on free hardware | Free hardware sleeps after about 48 hours idle | **Not a free InkPatch host.** A paid Docker Space could demo the UI, but storage and public source are poor fits |
| **Vercel / Netlify / Cloudflare Pages** | Static/serverless free tiers | No long-running Python/Tesseract process | No durable local filesystem | Function timeouts and cold starts | **Not suitable.** InkPatch is not a static site or a short serverless function |
| **Home machine + Tailscale** | Free Personal plan: up to 6 users, unlimited user devices, 50 tagged resources | Your hardware | Your disk | Your power/uptime | **Best private option.** No public exposure |
| **Home machine + Cloudflare Tunnel** | Tunnel free; Cloudflare Access free up to 50 users; domain registration costs money | Your hardware | Your disk | Your power/uptime | **Good public option**, but Cloudflare terminates TLS and sees proxied traffic |

### 4.2 Oracle Cloud Always Free in detail

Oracle is the recommended free host because it is the only option in this list
that gives a normal Linux VM with enough memory for the full OCR pipeline and
can run continuously within the free allowance.

As of November 2026, Oracle's Always Free documentation lists:

- Ampere A1 (Arm): 1,500 OCPU-hours and 9,000 GB-hours per month. For an
  Always Free tenancy that is equivalent to **2 OCPUs and 12 GB of memory**
  running continuously, as one VM or two smaller VMs.
- Up to two AMD `VM.Standard.E2.1.Micro` instances (about 1/8 OCPU and 1 GB
  each).
- 200 GB of combined boot and block volume storage.
- 10 TB of outbound data transfer per month.
- Resources must be created in the tenancy's **home region**.
- The free trial credit (if offered in your country) is separate and expires.

Caveats:

- Oracle reduced the Always Free A1 allowance from 4 OCPU / 24 GB to
  2 OCPU / 12 GB in 2026. Guides written before mid-2026 are stale.
- A1 capacity is often exhausted in popular regions. Try another availability
  domain, wait, or use a paid account; do not create multiple free accounts.
- Oracle may reclaim idle Always Free resources. Its documented idle test uses
  a seven-day window: 95th-percentile CPU below 20%, network utilization below
  20%, and (for A1 shapes) memory below 20%. A lightly used demo can qualify,
  so keep the instance monitored and check provider notices. Do not treat it
  as an SLA-backed production host.
- Always Free eligibility, PAYG differences, and region rules can change.
  Verify the current Oracle documentation before migrating real data.
- Sign-up commonly requires identity and payment-method verification even for
  the free tier. Use accurate details; multiple free accounts are prohibited.

### 4.3 Google Cloud e2-micro in detail

The GCP free VM is useful only as a small test box:

- 1 GB RAM is below the comfortable range for a 2400px image with OCR line
  recovery. Add 1–2 GB swap and reduce the image settings, or expect OOM kills.
- 1 GB of North America egress per month is roughly a few dozen large
  uploads/downloads; it is not enough for a public demo.
- The free VM is limited to three US regions and requires a billing account.
- Set a budget alert and do not attach GPUs or larger disks by accident.

If you already have GCP credits, use a paid `e2-small` (2 GB) or `e2-medium`
(4 GB) instead of fighting the free micro.

### 4.4 Why free PaaS platforms fail here

The common free container platforms are built for stateless web apps:

- **512 MB RAM** is too little for Pillow + OpenCV + NumPy + Tesseract on a
  phone photo. The process may be OOM-killed mid-OCR.
- **Ephemeral filesystems** delete `image.png`, `preview.png`, and `doc.json` on
  every restart, redeploy, or spin-down. InkPatch has no external database to
  fall back on.
- **Sleep/scale-to-zero** interrupts uploads and makes the first OCR request
  wait through a cold start.
- **Request timeouts** can cut off a long OCR pass; recovery is bounded but a
  large page can still take tens of seconds.
- **No persistent volume on the free plan** means a backup strategy cannot be
  implemented inside the platform.

A paid container plan with 1–2 GB RAM and a persistent volume can work, but at
that point a small VPS or Fly.io is usually cheaper and simpler.

Serverless container platforms (Cloud Run and similar) have the same problems:
no durable local filesystem, request timeouts, and cold starts. AWS and Azure
offer time-limited credits and trials rather than a permanent free VM of this
size, so they are not a long-term zero-cost plan for InkPatch.

---

## 5. Recommended deployment plans

### Plan A — Private home server + Tailscale (best privacy, zero monthly cost)

Use this for personal documents, client work, or anything you do not want on a
public host.

**Hardware:**

- Any x86_64 or ARM64 machine with **4 GB RAM or more** (8 GB comfortable).
- Raspberry Pi 4/5 with 4 GB or 8 GB works for light use; expect slower OCR.
- SSD or fast SD card; wired Ethernet if possible.
- UPS or at least clean shutdown handling for power loss.

**Software:**

1. Install Docker/Podman and the Compose plugin using the commands in
   `INSTALL.md`.
2. Run InkPatch with the default Compose file, bound to loopback.
3. Install Tailscale on the host and on your phone/laptop.
4. Enable HTTPS certificates in the tailnet, then serve the app privately:

   ```bash
   tailscale serve --bg 8000
   ```

   The exact URL is printed by `tailscale serve status`, for example
   `https://inkpatch-host.tailnet-name.ts.net`. On current clients, `serve`
   proxies to `http://127.0.0.1:8000` and adds Tailscale identity headers.
5. Set `TEXTEDIT_COOKIE_SECURE=1` in the Compose environment because the
   browser reaches the app over Tailscale HTTPS.
6. Do **not** forward ports 80/443/8000 on the router. Tailscale is outbound
   only and creates a private WireGuard mesh.

**Trade-offs:** Tailscale's coordination server sees device identities and
metadata, but not image content; traffic is end-to-end encrypted and relays
cannot decrypt it. The free Personal plan is for non-commercial use and has
user/tagged-resource limits. If you need public access, use Plan C instead of
Tailscale Funnel (Funnel is public, beta, and has non-configurable bandwidth
limits).

### Plan B — Public Oracle VM + Caddy + basic auth (best-effort free public demo)

Use this for a public demo or a small group of trusted users. The full
walkthrough is in section 7.

**Shape:** `VM.Standard.A1.Flex`, 2 OCPU / 12 GB, Ubuntu 24.04, 50–100 GB boot
volume, reserve a public IPv4, open only 22/80/443 in the security list.

**Stack:**

```text
Browser --HTTPS--> Caddy (443) --HTTP/loopback--> InkPatch (127.0.0.1:8000)
                         |
                         +-- basic auth or Cloudflare Access
                         +-- automatic Let's Encrypt certificate
```

Set `TEXTEDIT_COOKIE_SECURE=1`, keep `INKPATCH_BIND=127.0.0.1`, and use the
`inkpatch-data` volume or a bind mount under `/srv/inkpatch/data`.

**Outer auth:** Caddy `basic_auth` with a long unique password is acceptable
for a short-lived personal demo if you also add fail2ban or an edge rate limit.
For multiple users, use Cloudflare Access (free up to 50 users) or put the VM
behind Tailscale only (Plan A). Do not rely on the per-document cookie as a
login system.

### Plan C — Public from home + Cloudflare Tunnel + Access (no open ports)

Use this when you want a public URL but want to keep the hardware at home.

1. Run InkPatch on the home server, bound to `127.0.0.1:8000`.
2. Install `cloudflared` and create a named tunnel:

   ```bash
   cloudflared tunnel login
   cloudflared tunnel create inkpatch
   cloudflared tunnel route dns inkpatch inkpatch.example.com
   ```

3. Create the service configuration. The system service reads
   `/etc/cloudflared/config.yml`, so keep the credentials there too:

   ```bash
   sudo install -d -m 700 /etc/cloudflared
   sudo install -m 600 ~/.cloudflared/<TUNNEL-UUID>.json \
     /etc/cloudflared/<TUNNEL-UUID>.json
   sudoedit /etc/cloudflared/config.yml
   ```

   ```yaml
   tunnel: <TUNNEL-UUID>
   credentials-file: /etc/cloudflared/<TUNNEL-UUID>.json

   ingress:
     - hostname: inkpatch.example.com
       service: http://127.0.0.1:8000
     - service: http_status:404
   ```

4. Run it in the foreground once to test, then install it as a service:

   ```bash
   cloudflared --config /etc/cloudflared/config.yml tunnel run inkpatch
   sudo cloudflared --config /etc/cloudflared/config.yml service install
   sudo systemctl enable --now cloudflared
   ```

5. In Cloudflare Zero Trust, create a **self-hosted Access application** for
   `inkpatch.example.com` with an allow policy for your email (or an identity
   provider). Set a short session duration. This is the login layer; without
   it, the tunnel publishes the app to the internet.
6. Set `TEXTEDIT_COOKIE_SECURE=1` and keep the app on loopback.
7. Optional: add a Cloudflare WAF rule or rate limit for `/api/documents` and
   `/api/demo`. The app itself has no rate limiting.

**Trade-offs:** Cloudflare terminates TLS at its edge and can see proxied
request/response bodies, including images, while it applies Access and WAF
rules. That is a different privacy model from Tailscale. The free plan caps
proxied request bodies at 100 MB; InkPatch's default upload cap is 20 MB, so
that is not the limiting factor. You need a domain on Cloudflare. Home power,
ISP upload speed, and router stability become part of your uptime.

### Plan D — Low-cost paid fallback

If Oracle capacity is unavailable or you need an SLA-ish host:

| Option | Approx. cost | Notes |
| --- | --- | --- |
| Fly.io `shared-cpu-1x` 1 GB + 3 GB volume | ~US$7.15/month (US$6.70 machine + US$0.45 volume) | Add a volume for `/data`; set `TEXTEDIT_COOKIE_SECURE=1`; use a reverse proxy or Fly's TLS handler + auth. Fly waives personal bills under US$5/month, but this configuration is above that |
| Hetzner CX22 or comparable OVH VPS | ~€4–6/month | 2 vCPU / 4 GB is comfortable; full root access; install Docker + Caddy |
| Render Starter 512 MB | US$7/month | Still only 512 MB; not recommended for large images |
| Oracle PAYG A1 | Free allowance may be larger for PAYG, but overage is billable | Set a budget alert; do not assume the old 4 OCPU / 24 GB allowance |

For a paid host, the same rules apply: TLS, outer auth, persistent volume,
backups, and a single worker.

---

## 6. Resource sizing and tuning

### 6.1 Memory and CPU

These are practical starting points, not hard guarantees. OCR memory depends on
image size, language data, page segmentation, and how many line-recovery crops
are processed.

| Host | Verdict |
| --- | --- |
| 512 MB RAM | **Do not use** for real images. Tesseract/OpenCV can be OOM-killed. |
| 1 GB RAM | Small screenshots only, with swap, `TEXTEDIT_MAX_SIDE=1600`, `TEXTEDIT_OCR_TARGET_SIDE=1400`, `TEXTEDIT_MAX_UPLOAD_MB=8`, recovery budget 20–30 s. Expect occasional failure. |
| 2 GB RAM | Minimum comfortable for one user and moderate images. Keep one worker and `OMP_THREAD_LIMIT=1–2`. |
| 4 GB RAM | Recommended for phone photos, multi-language OCR, and line recovery. |
| 8 GB+ RAM | Comfortable for a small trusted group or large posters. Still run one worker; OCR is serialized. |

CPU: 2 vCPU is enough because OCR is serialized; more cores help only with
preprocessing and concurrent HTTP requests. ARM64 (Oracle A1) is fine if the
Python wheels install; current `opencv-python-headless` and NumPy releases
publish manylinux aarch64 wheels. If pip starts compiling OpenCV from source,
stop and use x86_64 or install the build toolchain deliberately.

### 6.2 Settings profiles

**Private host (4 GB+):**

```dotenv
TEXTEDIT_MAX_SIDE=2400
TEXTEDIT_MAX_UPLOAD_MB=20
TEXTEDIT_MAX_LINES=600
TEXTEDIT_TTL_HOURS=96
TEXTEDIT_COOKIE_SECURE=1
TEXTEDIT_OCR_TARGET_SIDE=2200
TEXTEDIT_OCR_LINE_RECOVERY=1
TEXTEDIT_OCR_RECOVERY_MAX_LINES=60
TEXTEDIT_OCR_RECOVERY_BUDGET=60
OMP_THREAD_LIMIT=2
```

**Public demo (2 GB):**

```dotenv
TEXTEDIT_MAX_SIDE=2000
TEXTEDIT_MAX_UPLOAD_MB=12
TEXTEDIT_MAX_LINES=400
TEXTEDIT_TTL_HOURS=48
TEXTEDIT_COOKIE_SECURE=1
TEXTEDIT_OCR_TARGET_SIDE=1800
TEXTEDIT_OCR_LINE_RECOVERY=1
TEXTEDIT_OCR_RECOVERY_MAX_LINES=40
TEXTEDIT_OCR_RECOVERY_BUDGET=45
OMP_THREAD_LIMIT=2
```

**Very small host (1 GB, experimental):**

```dotenv
TEXTEDIT_MAX_SIDE=1600
TEXTEDIT_MAX_UPLOAD_MB=8
TEXTEDIT_MAX_LINES=250
TEXTEDIT_TTL_HOURS=24
TEXTEDIT_COOKIE_SECURE=1
TEXTEDIT_OCR_TARGET_SIDE=1400
TEXTEDIT_OCR_LINE_RECOVERY=1
TEXTEDIT_OCR_RECOVERY_MAX_LINES=20
TEXTEDIT_OCR_RECOVERY_BUDGET=20
OMP_THREAD_LIMIT=1
```

Add 1–2 GB swap on a 1 GB host, but do not treat swap as a substitute for RAM:
Tesseract on swap is very slow.

### 6.3 Disk and bandwidth

- A document is typically a few megabytes: normalized PNG + preview + JSON.
  Large posters can be larger.
- With the default 96-hour TTL and a single user, 5–20 GB is usually plenty.
- A public demo with many uploads should use a shorter TTL (24–48 hours) and
  monitor disk usage.
- Uploads are capped by `TEXTEDIT_MAX_UPLOAD_MB`; downloads are PNGs. Oracle's
  10 TB egress is ample. GCP's 1 GB egress is not.
- Keep Docker logs bounded (section 8.4); an unbounded JSON log can fill a
  small disk.

---

## 7. Oracle Cloud deployment walkthrough

This is a complete single-VM setup. Replace `inkpatch.example.com` and the
placeholders with your values.

### 7.1 Create the instance

1. Create an Oracle Cloud account and choose a **home region** with A1 capacity.
2. Create a compartment and a VCN with a public subnet. Internet gateway and
   route table are created by the VCN wizard.
3. Create an instance:
   - Image: Ubuntu 24.04 (aarch64).
   - Shape: `VM.Standard.A1.Flex`, 2 OCPU, 12 GB (or 1 OCPU / 6 GB if capacity
     is tight).
   - Boot volume: 50–100 GB.
   - Add your SSH public key. Do not use a password login.
4. Reserve a public IPv4 address and attach it to the instance.
5. In the subnet's security list / network security group, allow:
   - TCP 22 from your own IP (not `0.0.0.0/0` if possible);
   - TCP 80 and 443 from `0.0.0.0/0`;
   - do **not** open 8000.
6. Point an A/AAAA DNS record at the reserved public IP.

### 7.2 Harden the host

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-v2 ufw fail2ban
sudo systemctl enable --now docker
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp   # or restrict this to your own IP
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Oracle's Ubuntu images may also have persistent iptables rules. If 80/443 are
still unreachable after opening the security list and UFW, inspect
`sudo iptables -L INPUT -n` and add the matching ACCEPT rules using Oracle's
current documentation.

Disable SSH password authentication and root login in
`/etc/ssh/sshd_config` (`PasswordAuthentication no`, `PermitRootLogin no`),
then restart SSH. Keep the Oracle console serial connection available in case
you lock yourself out.

Install Caddy 2.8 or newer from the official Caddy repository (recommended for
automatic HTTPS), or use the Nginx example in section 8.3. See
<https://caddyserver.com/docs/install#debian-ubuntu-raspbian> for the current
repository commands. The `caddy` package in some distribution repositories is
older and uses `basicauth` instead of `basic_auth`.

### 7.3 Install InkPatch

Copy the project to the server (Git clone, `scp`, or a release archive), then:

```bash
cd textedit
umask 077
touch .env
$EDITOR .env
```

Create `.env` with the production profile:

```dotenv
INKPATCH_BIND=127.0.0.1
INKPATCH_PORT=8000
TEXTEDIT_DATA_DIR=/data
TEXTEDIT_MAX_SIDE=2000
TEXTEDIT_MAX_UPLOAD_MB=12
TEXTEDIT_MAX_LINES=400
TEXTEDIT_TTL_HOURS=48
TEXTEDIT_COOKIE_SECURE=1
TEXTEDIT_OCR_TARGET_SIDE=1800
TEXTEDIT_OCR_LINE_RECOVERY=1
TEXTEDIT_OCR_RECOVERY_MAX_LINES=40
TEXTEDIT_OCR_RECOVERY_BUDGET=45
OMP_THREAD_LIMIT=2
```

Protect it:

```bash
chmod 600 .env
```

Build and start:

```bash
docker compose build
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8000/api/health
```

The health response should report OCR availability and the effective limits.
If it reports Tesseract missing, rebuild with the right language packages:

```bash
TESSERACT_LANGS="eng deu" EXTRA_FONTS="fonts-noto-core" docker compose build
docker compose up -d
```

### 7.4 Put Caddy in front

Generate a password hash:

```bash
caddy hash-password --plaintext 'use-a-long-unique-password'
```

Create `/etc/caddy/Caddyfile`:

```caddyfile
inkpatch.example.com {
    encode zstd gzip

    basic_auth {
        admin $2a$14$REPLACE_WITH_THE_GENERATED_HASH
    }

    reverse_proxy 127.0.0.1:8000

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer"
    }
}
```

Notes:

- `basic_auth` requires Caddy 2.8 or newer; older packages use `basicauth`.
  Confirm with `caddy version` and either install the current Caddy or use the
  Nginx example in section 8.3.
- Caddy obtains and renews the TLS certificate automatically. Ports 80 and 443
  must be reachable and the DNS record must point at the VM.
- Basic auth is a shared password with no logout or second factor. Use a long
  unique password, keep it out of shell history, and consider Cloudflare
  Access or Tailscale instead if you need per-user identity.
- The header block is deliberately conservative. Do not add a strict CSP
  without testing: the frontend has a small inline theme script.

Reload and verify:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -I https://inkpatch.example.com
```

From a browser, the site should prompt for the basic-auth password, then load
InkPatch. Check that `/api/health` works and that an upload sets an
`inkpatch_doc_<id>` cookie with the `Secure` flag.

### 7.5 Updates and rollback

```bash
cd textedit
docker compose build
docker compose up -d
```

Before updating, take a backup (section 9). Keep the previous image tag or a
copy of the project directory so you can rebuild the last known-good version.
Do not run `docker compose down -v` unless you intend to delete the document
volume.

### 7.6 Optional: native systemd instead of Docker

Docker is recommended because it pins Tesseract and system libraries. If you
must run natively, create a dedicated user, install the OS packages from
`INSTALL.md`, put the environment in a root-owned `EnvironmentFile`, and run:

```ini
[Unit]
Description=InkPatch
After=network.target

[Service]
User=inkpatch
WorkingDirectory=/srv/inkpatch
EnvironmentFile=/etc/inkpatch.env
ExecStart=/srv/inkpatch/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Keep Caddy in front and do not add `--workers`.

---

## 8. Docker and reverse-proxy reference

### 8.1 Compose production behavior

The repository `compose.yaml` is already suitable for a loopback-behind-proxy
deployment. It:

- publishes on `${INKPATCH_BIND:-127.0.0.1}:${INKPATCH_PORT:-8000}`;
- stores `/data` in the `inkpatch-data` volume;
- runs as uid 10001 with all capabilities dropped and
  `no-new-privileges:true`;
- restarts `unless-stopped`;
- has a health check against `/api/health`.

Do not change `INKPATCH_BIND` to `0.0.0.0` on a public host; Caddy or the tunnel
should connect over loopback.

### 8.2 Optional resource and log limits

Create `compose.override.yaml` next to `compose.yaml` if you want hard limits:

```yaml
services:
  inkpatch:
    mem_limit: 3g
    cpus: 1.5
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

Choose limits below the host's total RAM so the kernel does not kill Caddy or
SSH. On a 4 GB host, 3 GB for InkPatch leaves 1 GB for the OS and proxy. If OCR
is killed, raise the limit or lower the image settings; do not set it so low
that Tesseract cannot finish.

### 8.3 Nginx alternative

If you prefer Nginx, the equivalent core config is:

```nginx
server {
    listen 443 ssl http2;
    server_name inkpatch.example.com;

    ssl_certificate     /etc/letsencrypt/live/inkpatch.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/inkpatch.example.com/privkey.pem;

    client_max_body_size 25m;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "no-referrer" always;

    auth_basic "InkPatch";
    auth_basic_user_file /etc/nginx/inkpatch.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

Use `certbot` for certificates and `htpasswd -B` for the password file. The app
does not need WebSockets, but a longer `proxy_read_timeout` avoids cutting off
a slow OCR request.

### 8.4 Health and logs

```bash
curl -fsS http://127.0.0.1:8000/api/health
docker compose logs -f --tail=200
docker stats --no-stream
```

The health check is public only through the outer proxy. If you use Cloudflare
Access, either give your uptime monitor an Access service token or monitor the
origin over Tailscale/SSH instead of bypassing Access.

### 8.5 What not to expose

- Do not publish port 8000 on `0.0.0.0`.
- Do not mount the Docker socket into any container.
- Do not put the app in the same container as Caddy; keep the proxy separate.
- Do not add `--workers` or multiple replicas sharing a volume.
- Do not disable the Compose health check or the non-root user.

---

## 9. Storage, backup, restore, and retention

### 9.1 Where documents live

- Native install: `TEXTEDIT_DATA_DIR` (default `./data`).
- Compose: the `inkpatch-data` named volume mounted at `/data`.
- If you prefer a host directory, change the volume to a bind mount:

  ```yaml
  volumes:
    - /srv/inkpatch/data:/data
  ```

  The directory must be writable by uid/gid 10001 (the container user).

### 9.2 Backup

Stop the service briefly for a consistent copy, then archive the volume through
the Compose service so the project-prefixed volume name is resolved for you:

```bash
mkdir -p backups
docker compose stop inkpatch

docker compose run --rm -T --no-deps --entrypoint sh inkpatch \
  -c 'tar czf - -C /data .' \
  > "backups/inkpatch-$(date +%Y%m%d-%H%M).tgz"

docker compose start inkpatch
```

The Compose volume is normally named `<project>_inkpatch-data` (for example
`textedit_inkpatch-data`), not simply `inkpatch-data`. Check with
`docker volume ls` if you use a direct `docker run` helper instead.

For a bind mount, run `tar` while the service is stopped:

```bash
docker compose stop inkpatch
sudo tar czf "backups/inkpatch-$(date +%Y%m%d-%H%M).tgz" -C /srv/inkpatch/data .
docker compose start inkpatch
```

With the service stopped, keep backups off the host and encrypt them if they
contain real user images:

```bash
docker compose stop inkpatch
tar czf - -C /srv/inkpatch/data . | age -r <age-public-key> > backups/inkpatch-$(date +%F).tar.gz.age
docker compose start inkpatch
```

(Use `gpg` instead if that is what you already operate. Do not add a new Python
dependency for backups.)

### 9.3 Restore

Test the restore on a scratch host before relying on it. On the target:

```bash
docker compose stop inkpatch
docker compose run --rm -T --no-deps \
  -v "$PWD/backups:/backup:ro" \
  --entrypoint sh inkpatch \
  -c 'rm -rf /data/* && tar xzf /backup/inkpatch-YYYYMMDD-HHMM.tgz -C /data'
docker compose start inkpatch
```

This is destructive to the current volume. Confirm the archive name first.

### 9.4 The cookie/backup caveat

`doc.json` stores only the **hash** of the document access token. The raw token
lives in the browser cookie. A backup therefore restores the document files but
not the capability to open them from a different browser. After a disaster
recovery:

- the original browser can still open its documents if its cookie survived;
- a new browser without the cookie cannot open restored documents;
- there is no admin listing or token-recovery endpoint;
- export important final PNGs from the UI before retiring a browser profile.

This is intentional. Do not add a backup feature that stores raw tokens.

### 9.5 Retention

- Default: 96 hours without a change; cleanup at startup and hourly.
- Public demo: 24–48 hours is usually enough.
- Viewing or downloading does **not** reset the timer; saving/rendering/rescan
  does.
- Deleting a document is permanent. There is no server-side undo history.
- Restored folders may already be older than the TTL. The next startup cleanup
  can delete them immediately. If you intentionally restore for inspection,
  update the folder mtimes or accept that the cleanup will remove them.

### 9.6 Encryption at rest

The app does not encrypt documents. Options:

- full-disk encryption on the host (LUKS, cloud-provider encrypted volumes);
- an encrypted backup archive;
- a provider volume with encryption at rest enabled by default.

Full-disk encryption protects a stolen disk, not a compromised running host.
The operator and provider can still read documents while the service runs.

---

## 10. Authentication, access control, and abuse prevention

### 10.1 Outer auth choices

| Layer | Identity | Pros | Cons |
| --- | --- | --- | --- |
| **Tailscale Serve** | Tailscale identity / tailnet ACL | No public exposure; device-based; TLS automatic | Only tailnet members; free plan non-commercial; not a public demo |
| **Cloudflare Access** | Email OTP or IdP | Per-user login, MFA via IdP, free up to 50 users | Cloudflare sees proxied plaintext; domain required; config outside the app |
| **Caddy/Nginx basic auth** | Shared username/password | Simple, no external service | Shared password, no MFA/logout, brute-force risk; use a long password and fail2ban |
| **OAuth proxy** (oauth2-proxy, Authelia) | IdP | Per-user identity, MFA, groups | More moving parts; another container to patch |
| **InkPatch document cookie** | Per-document capability | Built in; no account DB | Not a login; only protects one document; creation endpoints are public |

For a public demo, **Cloudflare Access or an OAuth proxy is preferable** to
basic auth. For a private personal instance, **Tailscale Serve is preferable**
to publishing anything.

### 10.2 Rate limiting and abuse

InkPatch has no built-in rate limiting. Add it at the edge:

- Cloudflare Access/WAF rules for `/api/documents` and `/api/demo`.
- Caddy `rate_limit` requires a third-party module; avoid it unless you already
  build Caddy with plugins.
- Nginx `limit_req_zone` and `limit_conn_zone`.
- fail2ban on Caddy/Nginx logs for repeated 401s.
- A request-body limit at the proxy (InkPatch already enforces
  `TEXTEDIT_MAX_UPLOAD_MB`; the proxy limit is a second guard).
- Keep the public demo's TTL short and monitor disk usage.

### 10.3 CSRF and cookies

- Document cookies are `SameSite=Strict`, scoped to
  `/api/documents/<id>`, `HttpOnly`, and optionally `Secure`.
- The creation endpoints do not require a cookie, so a cross-site form could
  trigger an upload on a reachable host. Outer authentication is the primary
  protection. Do not add CORS permissiveness.
- Do not put document tokens in URLs, logs, or JavaScript-readable cookies.
- Set `TEXTEDIT_COOKIE_SECURE=1` for every HTTPS deployment. If you terminate
  TLS at a proxy but leave this at `0`, the cookie can travel over HTTP.

### 10.4 Security headers

Recommended at the proxy:

```text
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
```

Do not add a strict `Content-Security-Policy` without testing the frontend:
`app/static/index.html` contains a small inline theme script. If you need CSP,
use a nonce/hash or allow the inline script deliberately.

### 10.5 Public-demo etiquette

- State clearly that uploads are stored on the server for the TTL period.
- Ask users not to upload identity documents, medical records, or confidential
  material.
- Provide an abuse contact and a way to delete a document (the UI's delete
  action works for the browser that owns the cookie).
- Do not index the site; add `X-Robots-Tag: noindex` or a `robots.txt` if you
  do not want search engines to keep URLs.
- Do not promise anonymity: the hosting provider and proxy see metadata.

---

## 11. Monitoring and operations

### 11.1 Minimum monitoring

| Check | How | Alert threshold |
| --- | --- | --- |
| Process alive | `GET /api/health` via the proxy or a private monitor | 2–3 consecutive failures |
| OCR available | `/api/health` field for Tesseract/version | Tesseract missing or version changed unexpectedly |
| Disk | `df -h /` and `docker system df` | >80% used |
| Memory | `free -h`, `docker stats` | Sustained >90%, or OOM kills in `dmesg` |
| Container restarts | `docker inspect --format '{{.RestartCount}}'` | Any unexpected increase |
| TLS expiry | Caddy handles renewal; monitor the certificate | <14 days remaining |
| Backup | Run and verify a restore quarterly | Missing or failed archive |
| Provider quota | Oracle/GCP console + budget alerts | Approaching free limits or egress |

`GET /api/health?refresh=true` re-checks Tesseract; a plain request uses the
cached status. The endpoint is intentionally public for local health checks;
protect it behind the same outer auth as the app if you do not want it exposed.

### 11.2 Logging

- App logs go to stdout and are captured by Docker/systemd. They include
  document IDs and errors, not cookie values.
- Reverse proxies log client IPs, paths (including document UUIDs), status
  codes, and user agents. Set a retention period and restrict access.
- Do not enable request-body logging. It would capture images and text.
- Do not log `Authorization` or `Cookie` headers.

### 11.3 Update procedure

1. Read the release notes / `PROMPT.md` known issues.
2. Back up the data volume.
3. Pull the new source, rebuild the image, and restart.
4. Check `/api/health`, upload a demo image, edit a line, render, download.
5. Keep the previous image/project directory for rollback.

Pin base images and Python dependencies for reproducible builds. Rebuild at
least monthly for OS/Tesseract/OpenCV security updates; image decoders process
untrusted files.

### 11.4 Incident response

If you suspect abuse or a leaked capability:

1. Stop the public entry point (Caddy, tunnel, or Access policy) but keep the
   container running if you need to inspect it.
2. Preserve logs if your policy requires it; do not copy images unnecessarily.
3. Delete affected document folders or the whole data volume if the scope is
   unknown. There is no token-rotation endpoint; deleting the document is the
   reliable revocation.
4. Rotate the outer auth password, Cloudflare Access policy, tunnel
   credentials, and SSH keys as applicable.
5. Rebuild from a clean image if you suspect host compromise.
6. Tell affected users and follow your local breach-notification rules.

---

## 12. Optional OCR and AI sidecars

### 12.1 Start with Tesseract, not an LLM

The highest-value improvements are not a language model:

1. **Better Tesseract data.** Use `tessdata_best` or the appropriate
   `tessdata_fast` language packs; install the scripts you actually need.
2. **Better preprocessing.** The app already does perspective correction,
   deskewing, illumination flattening, threshold passes, and line recovery.
   Tune `TEXTEDIT_OCR_TARGET_SIDE`, `TEXTEDIT_OCR_MIN_CONFIDENCE`, and the
   recovery budget for the host.
3. **Page-segmentation mode.** Try automatic, sparse text, and single-column
   modes on difficult pages. Sparse text often helps posters and menus.
4. **Manual correction.** The UI already lets users fix lines, create boxes,
   rescan, and override style. A perfect OCR pass is not required for a usable
   editor.

An LLM cannot reliably produce pixel-accurate bounding boxes or preserve
typography. Do not put one in the critical OCR path.

### 12.2 Optional local OCR engine

If Tesseract is the limiting factor, a second local CPU OCR engine can be
evaluated behind a feature flag:

- **RapidOCR / PP-OCR ONNX** is a practical candidate: small models, CPU
  inference, Apache-2.0 licensing, and good printed-text accuracy.
- It would add `onnxruntime` (or an equivalent runtime) and new model assets,
  which the current project rules do not allow without approval.
- Run it as an optional sidecar or an optional extra, never as a mandatory
  dependency. Keep Tesseract as the fallback.
- Compare on the bundled corpus and on real failing images before changing the
  default.
- Watch memory: ONNX Runtime plus Tesseract plus OpenCV can exceed a 2 GB host.

A cloud OCR API (Google Vision, Azure Read, Amazon Textract) would improve some
hard cases but sends the image to a third party, has per-page cost, and breaks
the local-first guarantee. Treat it as a separate opt-in product, not a tuning
flag.

### 12.3 Optional small language model for correction suggestions

A small text-only LLM can suggest corrections after OCR, but it must not see
the image or auto-apply changes:

- **Qwen2.5-0.5B/1.5B Instruct** or **SmolLM2-360M/1.7B** in GGUF format via
  `llama.cpp` are plausible CPU-only choices.
- A 0.5B model quantized to 4-bit may fit in roughly 0.5–1 GB of RAM; a 1.5B
  model needs roughly 1.5–3 GB. Latency on a 2-vCPU VM is seconds per
  suggestion, not milliseconds.
- Feed only the OCR text and language context; do not send the image. Treat the
  text as sensitive.
- Return suggestions to the user for acceptance. Never rewrite a line
  automatically; OCR errors and hallucinated "corrections" are common.
- Keep the model optional and local. A cloud LLM API changes the privacy model
  and adds cost.

A small vision-language model (for example a 2B-class VLM) is **not**
recommended for this task: it may hallucinate text, cannot reliably produce
per-word boxes, and is slow on CPU. It would not replace Tesseract's word-level
TSV output.

### 12.4 Integration requirements for any future AI feature

If an OCR/AI sidecar is ever added:

- Explicit opt-in, off by default, documented in `README.md` and
  `HOSTING.md`.
- Local-only by default; if a remote API is supported, require a separate
  setting and disclose it per upload.
- No persistence of sidecar inputs/outputs beyond the existing document TTL.
- Timeouts and a fallback to Tesseract so a failed sidecar does not block
  editing.
- Bounded memory/CPU and a single concurrent request on small hosts.
- Tests for the fallback path, privacy default, and error handling.
- No new Python dependencies without the project owner's approval.

---

## 13. Hosted deployment checklist

Copy this into your runbook and tick every item before inviting users.

### Before deployment

- [ ] Chosen plan (A/B/C/D) and recorded why.
- [ ] Host has at least 2 GB RAM (4 GB recommended); if you insist on
      1 GB, configure swap and use the reduced profile.
- [ ] Disk has room for the image, Tesseract language data, and document TTL.
- [ ] DNS record points at the host or tunnel.
- [ ] TLS certificate path chosen (Caddy automatic, Cloudflare, or Tailscale).
- [ ] Outer auth chosen and tested (Tailscale, Access, basic auth, OAuth proxy).
- [ ] `TEXTEDIT_COOKIE_SECURE=1` in the environment.
- [ ] App bound to `127.0.0.1:8000`; only 80/443 (or the tunnel) exposed.
- [ ] Firewall/security list allows SSH only from trusted IPs.
- [ ] SSH password login disabled; keys backed up.
- [ ] Docker/Podman and the Compose plugin installed from a trusted source.
- [ ] Tesseract language packs and script fonts installed for expected users.
- [ ] `.env` and proxy credentials are mode `600`/`640`, not world-readable.

### After deployment

- [ ] `/api/health` reports OCR available and expected limits.
- [ ] Upload a test image; confirm a scoped `HttpOnly` cookie is set.
- [ ] Confirm the cookie has `Secure` over HTTPS and is absent from
      `document.cookie`.
- [ ] Confirm a second upload gets a different cookie.
- [ ] Confirm one document's cookie cannot access another document (`403`).
- [ ] Confirm upload, rescan, render, preview, download, and delete work.
- [ ] Confirm an unauthenticated request is blocked by the outer auth layer.
- [ ] Confirm viewing/downloading does not reset the TTL; saving does.
- [ ] Confirm the TTL cleanup removes the whole folder at startup and hourly.
- [ ] Confirm logs do not contain cookies, tokens, or request bodies.
- [ ] Confirm Docker logs are bounded and disk usage is visible.
- [ ] Take a backup and restore it on a scratch host.
- [ ] Test a restart/reboot: the container comes back and documents survive.
- [ ] Test the cold path on a phone: upload, edit, download.
- [ ] Add an uptime check and a disk/memory alert.
- [ ] Write the privacy notice, retention period, and abuse contact.
- [ ] Record the rollback steps and the last known-good image/tag.

---

## 14. Cost control

Even "free" tiers can generate charges:

- **Oracle:** free resources are limited to the home region and the Always Free
  shapes. A larger shape, extra block volume, reserved public IP, or paid
  account overage can bill. Set a budget alert and check the console after
  changes.
- **GCP:** the free e2-micro is limited to three regions, 30 GB standard disk,
  and 1 GB egress. Network egress, snapshots, static IPs, and larger disks can
  bill. Set a budget and quota alert.
- **Cloudflare:** Tunnel and Access have free tiers, but a domain, extra seats,
  or paid WAF/rate-limit features can cost money. Confirm the current plan.
- **Fly.io:** machines, volumes, rootfs of stopped machines, snapshots, and
  egress are billable. Auto-stop does not make storage free.
- **Render/Koyeb:** free plans may suspend rather than bill, but a paid plan
  selected at signup can charge immediately. Read the signup flow.
- **Hugging Face:** compute Spaces require a paid plan; free static Spaces do
  not run the backend.

Keep a monthly note of the provider, region, shape, volume size, and current
free-tier limits. Re-check after any provider email or console banner.

---

## 15. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `502` from Caddy/Nginx | App not running or wrong port | `docker compose ps`, `curl 127.0.0.1:8000/api/health`, check `INKPATCH_PORT` |
| Browser redirects to HTTP or cookie not set | Proxy/TLS mismatch | Use HTTPS end to end at the browser; set `TEXTEDIT_COOKIE_SECURE=1`; check `X-Forwarded-Proto` |
| `403` on every document route | Missing/wrong document cookie, or legacy document | Open the document in the browser that uploaded it; re-upload legacy documents without a token hash |
| OCR killed / container restarts | Out of memory | Lower `TEXTEDIT_MAX_SIDE`, `TEXTEDIT_OCR_TARGET_SIDE`, upload cap, and recovery budget; add RAM/swap |
| OCR is very slow | Large image, 1 vCPU, swap, or recovery budget | Lower settings; disable `TEXTEDIT_OCR_LINE_RECOVERY` if acceptable; use 2+ vCPU |
| Documents disappear after restart | Ephemeral filesystem or volume not mounted | Check the `/data` mount and `TEXTEDIT_DATA_DIR`; do not use a free PaaS with ephemeral disk |
| Disk fills up | Long TTL, many uploads, or unbounded Docker logs | Shorten `TEXTEDIT_TTL_HOURS`; prune old documents; set log rotation |
| `413` at the proxy | Proxy body limit below the app limit | Raise proxy limit (for example Caddy/Nginx `client_max_body_size`) or lower `TEXTEDIT_MAX_UPLOAD_MB` |
| `413` at Cloudflare | 100 MB free-plan body cap | Lower upload size; the app default is 20 MB |
| Certificate renewal fails | Port 80/443 blocked or DNS wrong | Open ports, check DNS, inspect `caddy`/`certbot` logs |
| Tailscale Serve URL not reachable | HTTPS not enabled or ACL denies | Enable HTTPS in the tailnet; check `tailscale serve status` and ACLs |
| Cloudflare Tunnel `502` | `cloudflared` cannot reach loopback | Check `service: http://127.0.0.1:8000` and that InkPatch is running |
| `docker compose` permission denied | User not in `docker` group | Re-login after `usermod -aG docker "$USER"`, or use `sudo` deliberately |
| ARM build compiles OpenCV for a long time | No aarch64 wheel for the Python version | Use a supported Python version, or build on x86_64; do not wait blindly |

---

## 16. References

Verify these before relying on a number. The hosting market changed
substantially in 2026.

- Oracle Always Free resources:
  <https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm>
- Oracle Free Tier FAQ:
  <https://www.oracle.com/cloud/free/faq/>
- Google Cloud Free Tier:
  <https://cloud.google.com/free/docs/free-cloud-features>
- Render free instances:
  <https://render.com/docs/free>
- Koyeb pricing FAQ:
  <https://www.koyeb.com/docs/faqs/pricing>
- Fly.io pricing:
  <https://fly.io/docs/about/pricing/>
- Hugging Face Spaces overview:
  <https://huggingface.co/docs/hub/spaces-overview>
- Tailscale Serve:
  <https://tailscale.com/kb/1312/serve>
- Tailscale pricing:
  <https://tailscale.com/pricing>
- Cloudflare Tunnel:
  <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/>
- Cloudflare Access:
  <https://developers.cloudflare.com/cloudflare-one/policies/access/>
- Cloudflare Zero Trust plans (free up to 50 users):
  <https://www.cloudflare.com/plans/zero-trust-services/>
- Caddy `basic_auth`:
  <https://caddyserver.com/docs/caddyfile/directives/basic_auth>
- Caddy automatic HTTPS:
  <https://caddyserver.com/docs/automatic-https>
- Docker Compose:
  <https://docs.docker.com/compose/>

---

## 17. Maintenance of this document

Re-check this plan when any of the following changes:

- a provider changes its free-tier limits, sleep behavior, or persistent-storage
  policy;
- InkPatch adds user accounts, rate limiting, encryption at rest, or a remote
  OCR/AI integration;
- the default Compose binding, cookie behavior, TTL, or storage layout changes;
- a hosted deployment reveals a new failure mode.

Record the check date at the top. Do not turn provider marketing numbers into
guarantees; link the official page and state the date you verified it.
