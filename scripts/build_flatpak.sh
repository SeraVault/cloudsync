#!/usr/bin/env bash
# Build the com.seravault.cloudsync Flatpak.
#
# Usage:
#   ./scripts/build_flatpak.sh [--install] [--bundle] [--regen-deps]
#
#   --install     Install the built app into the user Flatpak repo after building.
#   --bundle      Export a .flatpak bundle file after building.
#   --regen-deps  Re-run flatpak-pip-generator to update flatpak/python3-pip-deps.json.
#                 Requires flatpak-pip-generator.py (download from
#                 https://github.com/flatpak/flatpak-builder-tools/tree/master/pip).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

MANIFEST="${ROOT_DIR}/flatpak/com.seravault.cloudsync.json"
APP_ID="com.seravault.cloudsync"
BUILD_DIR="${ROOT_DIR}/.flatpak-builder"
REPO_DIR="${ROOT_DIR}/repo"
PIP_DEPS_JSON="${ROOT_DIR}/flatpak/python3-pip-deps.json"

INSTALL=false
BUNDLE=false
REGEN_DEPS=false

for arg in "$@"; do
    case "${arg}" in
        --install)    INSTALL=true ;;
        --bundle)     BUNDLE=true ;;
        --regen-deps) REGEN_DEPS=true ;;
        *)
            echo "Unknown argument: ${arg}"
            echo "Usage: $0 [--install] [--bundle] [--regen-deps]"
            exit 1
            ;;
    esac
done

# ── Dependency checks ────────────────────────────────────────────────────────

for cmd in flatpak-builder flatpak; do
    if ! command -v "${cmd}" &>/dev/null; then
        echo "Error: '${cmd}' is not installed or not on PATH." >&2
        exit 1
    fi
done

# ── Regenerate pip deps manifest (optional) ──────────────────────────────────
# flatpak/python3-pip-deps.json is committed to the repo and only needs
# regenerating when Python dependencies change. Run with --regen-deps to update.

if [[ "${REGEN_DEPS}" == true ]]; then
    GENERATOR="$(command -v flatpak-pip-generator.py 2>/dev/null || echo "")"
    if [[ -z "${GENERATOR}" ]]; then
        echo "Error: flatpak-pip-generator.py not found on PATH." >&2
        echo "Download from: https://github.com/flatpak/flatpak-builder-tools/tree/master/pip" >&2
        exit 1
    fi
    PIP_CMD="${ROOT_DIR}/.venv/bin/python3"
    if [[ ! -x "${PIP_CMD}" ]]; then
        PIP_CMD="python3"
    fi
    echo "==> Regenerating flatpak/python3-pip-deps.json ..."
    "${PIP_CMD}" "${GENERATOR}" \
        --runtime "org.gnome.Sdk//49" \
        --output "${PIP_DEPS_JSON%.json}" \
        --prefer-wheels watchdog,cryptography,protobuf \
        google-auth google-auth-oauthlib google-api-python-client \
        watchdog boto3 msal requests dropbox
    echo "==> Done — commit flatpak/python3-pip-deps.json before submitting to Flathub."
fi

if [[ ! -f "${PIP_DEPS_JSON}" ]]; then
    echo "Error: ${PIP_DEPS_JSON} not found. Run with --regen-deps to generate it." >&2
    exit 1
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
