"""Make the console's passphrase and the hash Azure stores.

    python3 tools/make_password.py ~/.config/pivot-ops/password

Writes the passphrase to that file (mode 600) and prints ONLY the scrypt hash,
which is what goes into OPS_PASSWORD_HASH. The passphrase is 12 groups of 6
characters from a 62-letter alphabet: about 428 bits.
"""
from __future__ import annotations

import os
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import hash_password  # noqa: E402

ALPHABET = string.ascii_letters + string.digits


def main(path: str) -> int:
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    phrase = "-".join("".join(secrets.choice(ALPHABET) for _ in range(6)) for _ in range(12))
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(phrase + "\n")
    print(hash_password(phrase))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
