# CloudSync Help

CloudSync keeps your local folders in sync with the cloud — automatically, in the background. Changes you make on disk are uploaded within seconds; changes from other devices are pulled down on a regular interval.

---

## Getting started

CloudSync is distributed as a Flatpak and works on any Linux distribution that supports it — Ubuntu, Fedora, Pop!\_OS, Linux Mint, Arch, and more. If Flatpak is not already set up on your system, visit [flathub.org/setup](https://flathub.org/setup) for distribution-specific instructions.

When you first launch CloudSync, a setup wizard will walk you through:

1. Choosing a cloud provider
2. Signing in
3. Picking a local folder and a cloud folder to sync

Once set up, CloudSync runs in your system tray and syncs continuously. You can add more accounts and folders at any time from the main window.

---

## Connecting a cloud account

### Google Drive

1. In the main window, click **+** next to **Cloud Accounts**.
2. Choose **Google Drive**.
3. A sign-in window opens — log in with your Google account and grant access.
4. CloudSync stores your token locally. You won't need to sign in again unless you revoke access.

### Dropbox

1. Click **+** next to **Cloud Accounts** and choose **Dropbox**.
2. A sign-in window opens — log in with your Dropbox account and click **Allow**.
3. Your token is stored locally.

### Microsoft OneDrive

1. Click **+** next to **Cloud Accounts** and choose **Microsoft OneDrive**.
2. A sign-in window opens — log in with your Microsoft account and approve the permissions.
3. Your token is stored locally.

### Amazon S3

You'll need an AWS Access Key ID and Secret Access Key for an IAM user with permissions on your target bucket.

1. Click **+** next to **Cloud Accounts** and choose **Amazon S3**.
2. Enter your **Access Key ID**, **Secret Access Key**, and **Region** (e.g. `us-east-1`).
3. Leave **Endpoint URL** blank for standard AWS S3.
4. Click **Connect** — CloudSync will verify your credentials.

**Minimum IAM policy (whole bucket):**

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

**Minimum IAM policy (prefix/folder only — recommended):**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET",
      "Condition": {
        "StringLike": { "s3:prefix": ["YOUR_PREFIX/*"] }
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

If your bucket uses **SSE-KMS** encryption, also add these permissions on the KMS key:

- `kms:Decrypt`
- `kms:Encrypt`
- `kms:GenerateDataKey`

### Backblaze B2

B2 uses the S3-compatible API.

1. In your Backblaze console, go to **App Keys** and create a key with read/write access to your bucket.
2. Note the **keyID**, **applicationKey**, and the **S3 Endpoint** shown on your bucket page (e.g. `s3.us-west-004.backblazeb2.com`).
3. In CloudSync, click **+** → **Backblaze B2**.
4. Enter your **keyID** as Access Key ID, **applicationKey** as Secret, and paste the endpoint URL.
5. Set the region to match the endpoint (e.g. `us-west-004`).

### Cloudflare R2

1. In the Cloudflare dashboard, go to **R2 → Manage R2 API Tokens** and create a token with Object Read & Write on your bucket.
2. Note the **Access Key ID**, **Secret Access Key**, and your **Account ID** from the R2 dashboard URL.
3. In CloudSync, click **+** → **Cloudflare R2**.
4. Enter your credentials and set the endpoint to `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.
5. Set region to `auto`.

---

## Adding a sync folder

1. Click **+** next to **Sync Folders** in the main window.
2. Choose the cloud account to sync with.
3. Click **Browse** to pick the remote bucket or folder.
4. Choose a local folder on your computer.
5. Click **Add Folder**.

CloudSync will do an initial sync immediately, then continue syncing in the background.

---

## Managing sync folders

Each folder row in the main window shows the current sync status. Click the row to expand it for detailed progress.

- **Sync now** — click the sync icon on the row to trigger an immediate sync for that folder only.
- **Edit** (pencil icon) — change the remote folder, sync interval, or conflict strategy for this folder.
- **Remove** (minus icon) — stop syncing this folder. Your local files are not deleted.

---

## Sync timing

CloudSync watches your local folder in real time using inotify. Local changes are uploaded within a few seconds of being saved.

For pulling down remote changes, CloudSync polls on a regular interval. You can set the interval globally in **Preferences**, or override it per folder in the folder's **Edit** dialog.

| Interval | When to use |
|---|---|
| 30 seconds | Active collaboration — you need near-real-time updates from others |
| 1 minute | Regular use — good balance of freshness and network activity |
| 5 minutes | Default — suitable for most personal use |
| 15 minutes | Low-activity folders or metered connections |

---

## Conflict resolution

A conflict happens when the same file is changed on your local device and in the cloud between syncs.

You can set a default strategy in **Preferences**, and override it per folder in the folder's **Edit** dialog.

### Keep both copies (default)

Your local version is renamed `filename.conflict_TIMESTAMP.ext` and uploaded alongside the remote copy. The remote version is then downloaded to the original filename. Both copies end up on all your devices — you decide which one to keep.

**Best for:** shared folders or any situation where you don't want to risk losing changes.

### Local copy wins

Your local version overwrites the remote file. Any changes made on other devices since the last sync are discarded.

**Best for:** folders you only edit from this device.

### Remote copy wins

The remote version overwrites your local file. Any local changes made since the last sync are discarded.

**Best for:** read-only or reference folders where the cloud is always authoritative.

---

## Preferences

Open **Preferences** from the menu in the top-right corner of the main window.

- **Default sync interval** — how often to check for remote changes (can be overridden per folder)
- **Default conflict resolution** — what to do when the same file is edited in two places (can be overridden per folder)
- **Desktop notifications** — show a notification when a sync completes or an error occurs
- **Start on login** — automatically launch CloudSync when you log in

---

## Subscription

CloudSync is free to use with one cloud provider and one sync folder. A subscription ($9.99/year) removes all limits.

### Subscribing

1. Visit [cloudsync.seravault.com](https://cloudsync.seravault.com/#pricing) and click **Subscribe & Download**.
2. Complete the checkout using the email address you want associated with your subscription.
3. Open CloudSync and go to **Preferences → Subscription**.
4. Enter the email address you used to subscribe and click **Sign In**.
5. CloudSync will verify your subscription and unlock all features immediately.

### Checking your subscription status

Open **Preferences → Subscription**. The status row at the top shows whether your subscription is active and when it renews. CloudSync re-validates your subscription at most once per week. If you just subscribed and need to activate immediately, sign in with your email.

### Signing out

To remove your account from this machine, open **Preferences → Subscription** and click **Sign Out**. The app reverts to free tier limits (1 provider, 1 sync folder). Your existing sync folders and cloud accounts remain intact.

### Renewing

Subscriptions renew automatically each year through Stripe. If your subscription lapses, CloudSync continues running but reverts to free tier limits. To renew, visit [cloudsync.seravault.com](https://cloudsync.seravault.com/#pricing) or contact [cloudsync@seravault.com](mailto:cloudsync@seravault.com).

### Subscription not activating?

- Make sure you're entering the exact email address used during checkout.
- Allow a minute or two after purchase before signing in — Stripe may take a moment to confirm payment.
- Check your internet connection — the app needs to reach our validation server.
- If the problem persists, email [cloudsync@seravault.com](mailto:cloudsync@seravault.com) with your purchase email.

---

## Troubleshooting

### AccessDenied errors with S3

1. Confirm the bucket name is spelled correctly.
2. Make sure `s3:ListBucket` is granted on the bucket ARN (not the object ARN).
3. Make sure `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` are granted on `arn:aws:s3:::YOUR_BUCKET/*`.
4. Check that the region matches the bucket's actual region.
5. If using SSE-KMS, ensure the KMS key permissions are in place.
6. Check for explicit Deny statements in your bucket policy or AWS Service Control Policies — an explicit deny overrides any allow.

### A folder shows an error and won't sync

- Check the **Recent Errors** section at the bottom of the main window for details.
- If the error mentions authentication, click **Edit** on the account and reconnect.
- For S3/B2/R2, verify your access key hasn't been rotated or revoked.

### Files aren't uploading after a save

- Make sure the folder is enabled (the toggle in the folder row is on).
- Check that there is enough free space in your cloud account.
- Look at Recent Errors for any upload failures.

### CloudSync isn't starting on login

- Open **Preferences** and toggle **Start on login** off, then on again.
