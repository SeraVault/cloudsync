# SeraVault CloudSync

A two-way cloud sync app for the Linux desktop with a native GNOME interface. Sync your local folders to the cloud automatically — in the background, with per-folder control over timing and conflict handling.

CloudSync is free to use with one cloud provider and one sync folder. **CloudSync Pro** ($9.99/year) removes all limits.

| | Free | Pro |
|---|---|---|
| Cloud providers | 1 | Unlimited |
| Sync folders | 1 | Unlimited |
| Two-way sync | ✓ | ✓ |
| Desktop notifications | ✓ | ✓ |
| Automatic updates | ✓ | ✓ |

Subscribe at [cloudsync.seravault.com](https://cloudsync.seravault.com). The app continues to function after a subscription lapses — free tier limits apply, but updates continue for all users regardless of subscription status.

---

## Supported providers

| Provider | Auth method |
|---|---|
| Google Drive | OAuth 2.0 (in-app browser) |
| Dropbox | OAuth 2.0 PKCE (in-app browser) |
| Amazon S3 | Access Key + Secret |
| Backblaze B2 | S3-compatible Access Key + Secret |
| Cloudflare R2 | S3-compatible Access Key + Secret |

Multiple providers can be connected simultaneously. Each sync folder is mapped independently to a provider.

---

## Features

### Sync
- **Two-way sync** — local changes upload immediately; remote changes are pulled on a configurable interval
- **Real-time local watching** — `inotify` via watchdog; uploads within ~2 seconds of a file save, with debouncing to handle rapid editor writes
- **Per-folder sync** — trigger an immediate sync for a single folder from the main window without running a full sync
- **Per-folder timing** — each folder can override the global sync interval, or inherit it
- **Multiple accounts** — connect Google Drive, Dropbox, S3, and OneDrive at the same time

### Conflict resolution
Three strategies, configurable globally and overridable per folder:

| Strategy | What happens |
|---|---|
| **Keep both copies** (default) | Your local version is renamed `filename.conflict_TIMESTAMP.ext` and uploaded. The remote version is downloaded to the original filename. Both copies end up on all devices — you resolve manually. |
| **Local copy wins** | Your local version overwrites the remote. Remote changes since the last sync are discarded. |
| **Remote copy wins** | The remote version overwrites your local file. Local changes since the last sync are discarded. |

### Interface
- **Native GNOME UI** — GTK 4 + libadwaita, follows your system light/dark theme
- **System tray** — lives quietly in the tray; the main window can be closed without quitting
- **Setup wizard** — guides you through connecting a provider and choosing a sync folder on first run
- **Start on login** — XDG autostart toggle in Preferences
- **Desktop notifications** — sync results and errors via the system notification daemon

### S3-compatible storage
When connecting an S3 account the setup wizard offers one-click presets for:
- **Amazon S3** — standard AWS endpoint
- **Backblaze B2** — pre-filled B2 endpoint; adjust the region code to your bucket
- **Cloudflare R2** — pre-filled R2 endpoint; replace the account ID placeholder

After connecting, use the **Browse** button in the folder picker to navigate buckets and subfolders visually.

---

## Installation

CloudSync is distributed as a Flatpak via Flathub. See [cloudsync.seravault.com/#install](https://cloudsync.seravault.com/#install) for full instructions.

```bash
flatpak install flathub com.seravault.cloudsync
```

### Upgrade to CloudSync Pro

1. Subscribe via [cloudsync.seravault.com](https://cloudsync.seravault.com) or open **Preferences → Subscription** in the app and click **Subscribe**.
2. Complete checkout — use the email address you want linked to your subscription.
3. Open **Preferences → Subscription**, enter that email, and click **Sign In**.
4. CloudSync verifies your subscription and unlocks all features immediately.

### Install from Flatpak bundle (offline)

```bash
flatpak install --user com.seravault.cloudsync.flatpak
```

---

## First run

1. Launch **CloudSync** from your application menu (or run `cloudsync` in a terminal).
2. The setup wizard opens — choose a provider, sign in, and pick a local folder to sync.
3. Sync starts automatically after setup. Add more providers or folders any time from the main window.
4. _(Optional)_ Open **Preferences → Subscription** and sign in with your email to activate CloudSync Pro.

---

## Google Drive setup

OAuth credentials are bundled — no Google Cloud Console setup required for end users. Sign in with your Google account when prompted.

**For developers building from source:**

Place a `client_secret.json` (standard Google Desktop OAuth format) next to `src/cloudsync/core/auth.py`, or set environment variables:

```bash
export CLOUDSYNC_GOOGLE_CLIENT_ID=your_client_id
export CLOUDSYNC_GOOGLE_CLIENT_SECRET=your_client_secret
```

To obtain credentials:
1. Go to [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
2. Create an **OAuth 2.0 Client ID** — type **Desktop app**
3. Enable the **Google Drive API** for your project
4. Download the JSON file and save it as `src/cloudsync/core/client_secret.json`

---

## Dropbox setup

OAuth credentials are bundled. Sign in with your Dropbox account when prompted — no developer setup required.

---

## Amazon S3 / B2 / R2 setup

### 1. Create IAM credentials (AWS S3)

1. In AWS IAM, create a user (e.g. `cloudsync-user`).
2. Attach a policy with the minimum required permissions (see below).
3. Create an access key for that user and copy the key ID and secret.

### 2. Minimum IAM policy

**Whole bucket:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET/*"
    }
  ]
}
```

**Prefix only (recommended — limits access to one folder):**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET",
      "Condition": {
        "StringLike": {
          "s3:prefix": ["YOUR_PREFIX/*"]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET/YOUR_PREFIX/*"
    }
  ]
}
```

### 3. SSE-KMS buckets

If your bucket uses SSE-KMS encryption, also grant on the KMS key:

- `kms:Decrypt`
- `kms:Encrypt`
- `kms:GenerateDataKey`

### 4. Connect in CloudSync

1. In the setup wizard, choose **Amazon S3**, **Backblaze B2**, or **Cloudflare R2**.
2. Enter your Access Key ID, Secret Access Key, and region.
3. For B2/R2, the endpoint URL is pre-filled — adjust if your bucket is in a different region.
4. Click **Connect** — credentials are verified without needing bucket access (uses STS `GetCallerIdentity` for AWS; skipped for B2/R2).
5. On the next screen, use the **Browse** button to navigate to your bucket and choose a folder.

### 5. Troubleshooting AccessDenied

1. Confirm the bucket name and prefix are correct.
2. Check that `s3:ListBucket` is on the bucket ARN and object actions are on `bucket/*`.
3. Verify the region matches the bucket's actual region.
4. Check for explicit Deny statements in bucket policies or SCPs — these override Allow rules.
5. Add KMS permissions if the bucket uses SSE-KMS.

---

## Configuration

Settings live in `~/.config/cloudsync/config.json`. Most are available through **Preferences** (hamburger menu → Preferences):

- **Default sync interval** — how often to poll for remote changes (30 s / 1 min / 5 min / 15 min)
- **Default conflict resolution** — Keep both / Local wins / Remote wins
- **Desktop notifications** — on/off
- **Start on login** — registers an XDG autostart entry

Per-folder overrides for both sync interval and conflict resolution are available in the **Edit** dialog for each folder.

Credentials are stored separately and are readable only by the current user (`chmod 600`):

| File | Contents |
|---|---|
| `~/.config/cloudsync/token.json` | Google Drive OAuth token |
| `~/.config/cloudsync/s3_credentials.json` | S3 / B2 / R2 access key + secret |
| `~/.config/cloudsync/dropbox_token.json` | Dropbox OAuth token |

---

## Architecture

```
src/cloudsync/
├── app.py              # Adw.Application — wires auth, engines, and UI
├── main.py             # Application entry point
├── core/
│   ├── auth.py         # Google OAuth2
│   ├── dropbox_auth.py # Dropbox PKCE OAuth
│   ├── s3_auth.py      # S3/B2/R2 credential management
│   ├── autostart.py    # XDG autostart
│   ├── activity_log.py # Persistent sync event log (capped at 500 entries)
│   ├── config.py       # Config + SyncFolder dataclasses, atomic JSON persistence
│   └── license.py      # Stripe subscription validation + free tier enforcement
├── sync/
│   ├── base.py         # CloudStorageClient abstract base
│   ├── engine.py       # Two-way sync engine, watcher thread, conflict resolution
│   ├── gdrive.py       # Google Drive API client (auto-retry on 429/5xx)
│   ├── dropbox.py      # Dropbox API client
│   ├── s3.py           # S3/B2/R2 boto3 client (adaptive retry)
│   └── watcher.py      # watchdog inotify wrapper
├── ui/
│   ├── window.py       # Main application window + FolderRow widgets
│   ├── preferences.py  # Preferences dialog
│   ├── setup_wizard.py # First-run setup wizard
│   ├── edit_folder_dialog.py   # Per-folder settings editor
│   ├── edit_account_dialog.py  # Re-authenticate / update credentials
│   ├── cloud_folder_picker.py  # Remote folder browser dialog
│   └── auth_dialog.py  # Embedded WebKit OAuth window
└── notifications.py    # Desktop notification helpers
```

To add a new provider: implement `CloudStorageClient` (see `sync/base.py`), add an auth class under `core/`, and wire both into `app.py`.

---

## Building from source

```bash
git clone https://github.com/SeraVault/cloudsync.git
cd cloudsync
```

**System dependencies (Ubuntu / Debian / Mint):**

```bash
sudo apt install \
    python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 gir1.2-adw-1 \
    gir1.2-webkit2-4.1          # or gir1.2-webkit-6.0
```

### Development virtualenv

PyGObject requires system GTK libraries that cannot be installed via pip alone. Create the venv with `--system-site-packages` so it inherits the system-installed `gi` bindings:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .
```

After activation, run the app directly:

```bash
cloudsync
```

### Building the Flatpak

Requires `flatpak-builder` and the GNOME SDK:

```bash
flatpak install flathub org.gnome.Sdk//49 org.gnome.Platform//49
```

Then run the build script:

```bash
# Build only
./scripts/build_flatpak.sh

# Build and install for the current user
./scripts/build_flatpak.sh --install

# Build and export a .flatpak bundle file
./scripts/build_flatpak.sh --bundle
```

`flatpak/python3-pip-deps.json` is committed to the repo and contains all pip dependencies as URL + SHA-256 entries suitable for Flathub review. To regenerate it after changing Python dependencies, download [`flatpak-pip-generator.py`](https://github.com/flatpak/flatpak-builder-tools/tree/master/pip) and run:

```bash
./scripts/build_flatpak.sh --regen-deps
```

Commit the updated `flatpak/python3-pip-deps.json` before submitting a Flathub PR.

**Requirements:**

| Dependency | Version |
|---|---|
| Python | ≥ 3.10 |
| GTK 4 | ≥ 4.10 |
| libadwaita | ≥ 1.4 |
| WebKit2GTK | 4.1 or 6.0 — optional, enables in-app OAuth login |

---

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md) — free for personal and other
noncommercial use; commercial use is not permitted. Contact
cloudsync@seravault.com for commercial licensing.
