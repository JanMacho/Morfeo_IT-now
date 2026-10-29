#!/usr/bin/env python3
"""Installs Apache JMeter and its plugins into the generator image, with every download checked.

The versions and checksums come from plugins.json, so the same lab always builds the same engine:

  * JMeter is downloaded from the Apache archive and checked against the SHA-512 published next to it;
  * every plugin jar is downloaded from Maven Central and checked against the SHA-256 in plugins.json.

Nothing is downloaded when a test runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

LOCK = Path(__file__).with_name("plugins.json")
MAVEN = "https://repo1.maven.org/maven2"


RETRY_STATUS = (429, 500, 502, 503, 504)


def download(url: str, attempts: int = 8) -> bytes:
    """Maven Central answers 429 when asked too fast, so those answers (and timeouts) are retried. A certificate
    problem is not: it will not fix itself."""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == attempts:
                raise SystemExit(f"Cannot download {url}: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLError):
                raise SystemExit(
                    f"Cannot download {url}: {e.reason}\n"
                    "The network seems to inspect HTTPS traffic with its own certificate authority. Give the build "
                    "that certificate (a .crt or .pem file) with the MORFEO_BUILD_CA environment variable and run "
                    "`up` again.") from e
            if attempt == attempts:
                raise SystemExit(f"Cannot download {url}: {e}") from e
        time.sleep(min(5 * attempt, 30))
    raise AssertionError("unreachable")


def install_jmeter(spec: dict, home: Path) -> None:
    url = spec["url"]
    data = download(url)
    published = download(url + ".sha512").decode("ascii", "replace").split()[0].lower()
    actual = hashlib.sha512(data).hexdigest()
    if actual != published:
        raise SystemExit(f"JMeter checksum mismatch: expected {published}, got {actual}")
    archive = Path("/tmp/jmeter.tgz")
    archive.write_bytes(data)
    with tarfile.open(archive) as tf:
        tf.extractall(home.parent, filter="data")
    (home.parent / f"apache-jmeter-{spec['version']}").rename(home)
    archive.unlink()
    for unused in ("docs", "printable_docs"):
        _remove(home / unused)
    print(f"JMeter {spec['version']} installed (SHA-512 verified)")


def _remove(path: Path) -> None:
    if path.is_dir():
        for child in path.iterdir():
            _remove(child)
        path.rmdir()
    elif path.exists():
        path.unlink()


def install_artifact(art: dict, ext: Path) -> None:
    group, name, version = art["coordinates"].split(":")
    url = f"{MAVEN}/{group.replace('.', '/')}/{name}/{version}/{name}-{version}.jar"
    data = download(url)
    actual = hashlib.sha256(data).hexdigest()
    if actual != art["sha256"]:
        raise SystemExit(f"{name}-{version}.jar checksum mismatch: expected {art['sha256']}, got {actual}")
    (ext / f"{name}-{version}.jar").write_bytes(data)
    print(f"  {name}-{version}.jar (SHA-256 verified)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugins", default="", help="comma-separated plugin ids from plugins.json")
    ap.add_argument("--home", default="/opt/apache-jmeter")
    args = ap.parse_args()
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    wanted = [p for p in args.plugins.split(",") if p]
    unknown = [p for p in wanted if p not in lock["plugins"]]
    if unknown:
        raise SystemExit(f"Unknown plugin(s): {', '.join(unknown)}. Available: {', '.join(sorted(lock['plugins']))}")
    home = Path(args.home)
    install_jmeter(lock["jmeter"], home)
    ext = home / "lib" / "ext"
    artifacts = list(lock["common"]) if wanted else []
    for pid in wanted:
        artifacts += lock["plugins"][pid]["artifacts"]
    print(f"Plugins: {', '.join(wanted) or 'none'}")
    for art in artifacts:
        install_artifact(art, ext)


if __name__ == "__main__":
    sys.exit(main())
