#!/usr/bin/env bash
# Refuse to hand the DRM framebuffer to a second application.
#
# The Python renderer and the Qt runtime both drive the panel through DRM/KMS
# and only one process may hold DRM master. Starting the second application
# while the first is running does not fail loudly: the panel simply stays blank
# or the new process dies with an unhelpful libdrm error. This guard turns that
# into a clear message.
#
# Usage: guard-drm-master.sh [service-name]
# Exits 0 when the framebuffer is free, 1 when another owner is active.

set -Eeuo pipefail

readonly SERVICE="${1:-${ITUNER_DRM_OWNER_SERVICE:-ituner-sdr.service}}"

# Not a systemd host (macOS development, a container, a test image): there is no
# service to compete with, so the guard is a no-op.
if ! command -v systemctl >/dev/null 2>&1; then
    exit 0
fi

if systemctl is-active --quiet "${SERVICE}"; then
    printf 'refusing to start: %s is active and owns the DRM framebuffer\n' "${SERVICE}" >&2
    printf 'stop it first:  sudo systemctl stop %s\n' "${SERVICE}" >&2
    exit 1
fi

exit 0
