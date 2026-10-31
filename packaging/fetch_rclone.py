"""Download the pinned rclone into packaging/bin. Run from the repo root.

To upgrade, change both values below. The hash comes from
https://downloads.rclone.org/<version>/SHA256SUMS.
"""

import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path

VERSION = "v1.75.2"
SHA256 = "8a9db2bad1dc79a65b3348fd857069598e2aab7cf4d118b77b1df23055aff818"

archive = f"rclone-{VERSION}-windows-amd64"
url = f"https://downloads.rclone.org/{VERSION}/{archive}.zip"
output = Path("packaging/bin")

# The download server rejects urllib's default User-Agent.
request = urllib.request.Request(url, headers={"User-Agent": "rclone-gui-build"})
with urllib.request.urlopen(request) as response:
    data = response.read()

actual = hashlib.sha256(data).hexdigest()
if actual != SHA256:
    raise SystemExit(f"Checksum mismatch for {url}: got {actual}, expected {SHA256}")

output.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(io.BytesIO(data)) as zip_file:
    (output / "rclone.exe").write_bytes(zip_file.read(f"{archive}/rclone.exe"))
    # Carries rclone's MIT license, which must ship with the binary.
    (output / "rclone-README.txt").write_bytes(zip_file.read(f"{archive}/README.txt"))
print(f"rclone {VERSION} written to {output}")
