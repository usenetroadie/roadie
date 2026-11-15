"""Safari/iOS does not report `credProps`, so Authelia stores new passkeys with discoverable=0 and
passkey login then fails. The setup requires discoverable passkeys anyway, so flip the flag."""

import sqlite3
import sys
from pathlib import Path


def fix_discoverable(db_path: Path) -> int:
    """Set discoverable=1 on all passkeys that have 0. Returns changed rows; never raises."""
    if not Path(db_path).is_file():
        return 0
    try:
        con = sqlite3.connect(str(db_path), timeout=5)
        try:
            with con:
                return con.execute("UPDATE webauthn_credentials SET discoverable = 1 WHERE discoverable = 0").rowcount
        finally:
            con.close()
    except sqlite3.Error as e:  # e.g. locked or not created yet; the SQL is fixed, no secrets
        print(f"passkey: check skipped ({type(e).__name__}: {e})", file=sys.stderr)
        return 0
