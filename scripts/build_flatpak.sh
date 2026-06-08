#!/usr/bin/env bash
# Build the com.seravault.cloudsync Flatpak.
#
# Usage:
#   ./scripts/build_flatpak.sh [--install] [--bundle]
#
#   --install   Install the built app into the user Flatpak repo after building.
#   --bundle    Export a .flatpak bundle file after building.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

MANIFEST="${ROOT_DIR}/flatpak/com.seravault.cloudsync.json"
APP_ID="com.seravault.cloudsync"
BUILD_DIR="${ROOT_DIR}/.flatpak-builder"
REPO_DIR="${ROOT_DIR}/repo"
PIP_DEPS_DIR="${ROOT_DIR}/flatpak/pip-deps"

INSTALL=false
BUNDLE=false

for arg in "$@"; do
    case "${arg}" in
        --install) INSTALL=true ;;
        --bundle)  BUNDLE=true ;;
        *)
            echo "Unknown argument: ${arg}"
            echo "Usage: $0 [--install] [--bundle]"
            exit 1
            ;;
    esac
done

# ── Dependency checks ────────────────────────────────────────────────────────

for cmd in flatpak-builder flatpak pip; do
    if ! command -v "${cmd}" &>/dev/null; then
        echo "Error: '${cmd}' is not installed or not on PATH." >&2
        exit 1
    fi
done

# ── Download pip dependencies (if needed) ────────────────────────────────────
# The manifest sources pip packages from flatpak/pip-deps/ as a local dir.
# Re-run this step whenever requirements change.

if [[ ! -d "${PIP_DEPS_DIR}" ]] || [[ -z "$(ls -A "${PIP_DEPS_DIR}")" ]]; then
    echo "==> Downloading pip dependencies into flatpak/pip-deps/ ..."
    mkdir -p "${PIP_DEPS_DIR}"
    # Use the venv pip if available so the resolver matches the target Python.
    PIP_CMD=".venv/bin/pip"
    if [[ ! -x "${ROOT_DIR}/${PIP_CMD}" ]]; then
        PIP_CMD="pip"
    fi
    "${ROOT_DIR}/${PIP_CMD}" download \
        --dest "${PIP_DEPS_DIR}" \
        --python-version "3.13" \
        --only-binary=:all: \
        --platform linux_x86_64 \
        --implementation cp \
        --abi cp313 \
        setuptools \
        google-auth \
        google-auth-oauthlib \
        google-api-python-client \
        watchdog \
        boto3 \
        msal \
        requests \
        dropbox
else
    echo "==> pip-deps/ already populated — skipping download (delete to force re-download)."
fi

# ── Build ────────────────────────────────────────────────────────────────────

echo "==> Building Flatpak ..."
flatpak-builder \
    --force-clean \
    --state-dir="${BUILD_DIR}" \
    --repo="${REPO_DIR}" \
    "${BUILD_DIR}/build" \
    "${MANIFEST}"

# ── Install ──────────────────────────────────────────────────────────────────

if [[ "${INSTALL}" == true ]]; then
    echo "==> Installing ${APP_ID} for current user ..."
    flatpak --user remote-add --no-gpg-verify --if-not-exists \
        cloudsync-local "${REPO_DIR}"
    flatpak --user install --reinstall --assumeyes \
        cloudsync-local "${APP_ID}"
fi

# ── Bundle ───────────────────────────────────────────────────────────────────

if [[ "${BUNDLE}" == true ]]; then
    BUNDLE_FILE="${ROOT_DIR}/${APP_ID}.flatpak"
    echo "==> Exporting bundle to ${BUNDLE_FILE} ..."
    flatpak build-bundle \
        "${REPO_DIR}" \
        "${BUNDLE_FILE}" \
        "${APP_ID}"
    echo "==> Bundle written to ${BUNDLE_FILE}"
fi

echo "==> Done."
