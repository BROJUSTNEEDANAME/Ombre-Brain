#!/usr/bin/env python3
"""Persist the Caddy-derived private public route as a systemd environment."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

from public_site import resolve_public_site_url


def main() -> int:
    caddy_path = sys.argv[1] if len(sys.argv) > 1 else "/etc/caddy/Caddyfile"
    output_path = Path(
        sys.argv[2]
        if len(sys.argv) > 2
        else "/etc/systemd/system/ombre-brain.service.d/20-site-url.conf"
    )
    site_url = resolve_public_site_url({}, caddy_path)
    if not site_url:
        raise SystemExit("Cannot infer Ombre public route from Caddy")
    if not re.fullmatch(r"https://[A-Za-z0-9._:-]+(?:/[A-Za-z0-9._~-]+)*", site_url):
        raise SystemExit("Inferred Ombre public route contains unsafe characters")

    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = f'[Service]\nEnvironment="OMBRE_SITE_URL={site_url}"\n'
    handle, temporary = tempfile.mkstemp(
        prefix=".site-url-", dir=output_path.parent, text=True
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("SITE-URL-CONFIGURED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
