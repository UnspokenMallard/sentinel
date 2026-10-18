#!/usr/bin/env python3
"""Sampled file restore drill for mac-backup (BACKUP-DRILL-DESIGN.md, part B).

The monthly drill used to restore only three SQLite copies. This picks random files from every
backed-up tree, so mac-backup can restore them and compare each with the live file.
Runs under /usr/bin/python3 (3.9) like the rest of mac-backup: stdlib only, no 3.10+ syntax.

  backup-drill-sample.py pick N BEFORE_EPOCH EXCLUDES_FILE SOURCE...   print N paths, one per line
  backup-drill-sample.py verify RESTORE_DIR < paths                     exit 1 on a missing or differing file

`pick` takes only regular files last modified before BEFORE_EPOCH (when the backup started), so a
file edited since cannot count as a mismatch. Half the sample comes from files changed in the last
30 days (what is most likely to matter), half from everything.
"""
import fnmatch
import hashlib
import os
import random
import sys
import time

RECENT_S = 30 * 86400


def load_excludes(path):
    pats = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
    return pats


def excluded(path, name, pats):
    # restic semantics, for the forms excludes.txt uses: a bare name matches any path component,
    # a pattern with a slash matches the whole path (with ** for any depth).
    for p in pats:
        if "/" in p:
            if fnmatch.fnmatch(path, p.replace("**", "*")) or path == p or path.startswith(p.rstrip("/") + "/"):
                return True
        elif fnmatch.fnmatch(name, p):
            return True
    return False


def candidates(sources, pats, before):
    for src in sources:
        if os.path.isfile(src) and not os.path.islink(src):
            if os.stat(src).st_mtime < before:
                yield src, os.stat(src).st_mtime
            continue
        for root, dirs, files in os.walk(src):
            if "CACHEDIR.TAG" in files:  # mac-backup runs restic with --exclude-caches
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not excluded(os.path.join(root, d), d, pats)]
            for f in files:
                p = os.path.join(root, f)
                if excluded(p, f, pats) or os.path.islink(p):
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime < before and os.access(p, os.R_OK) and os.path.isfile(p):
                    yield p, st.st_mtime


def pick(n, before, excludes, sources):
    """At least one file per source (so a small tree like the Notes vault is never skipped by ~/code's
    size), about n in all, half of each share from recently changed files."""
    pats = load_excludes(excludes)
    share = max(1, -(-n // len(sources)))
    chosen = set()
    for src in sources:
        everything, recent = [], []
        for p, mtime in candidates([src], pats, before):
            everything.append(p)
            if mtime >= before - RECENT_S:
                recent.append(p)
        mine = set(random.sample(recent, min(len(recent), share // 2)))
        rest = [p for p in everything if p not in mine]
        mine.update(random.sample(rest, min(len(rest), share - len(mine))))
        chosen.update(mine)
    return sorted(chosen)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(restore_dir, paths):
    bad = []
    for p in paths:
        restored = restore_dir + p
        if not os.path.isfile(restored):
            bad.append("missing " + p)
        elif sha256(restored) != sha256(p):
            bad.append("differs " + p)
    return bad


def main(argv):
    if len(argv) >= 5 and argv[1] == "pick":
        for p in pick(int(argv[2]), float(argv[3]), argv[4], argv[5:]):
            print(p)
        return 0
    if len(argv) == 3 and argv[1] == "verify":
        paths = [line.rstrip("\n") for line in sys.stdin if line.strip()]
        bad = verify(argv[2], paths)
        if not paths:
            print("no files were sampled")
            return 1
        if bad:
            print("%d of %d sampled files failed: %s" % (len(bad), len(paths), "; ".join(bad[:5])))
            return 1
        print("%d sampled files restored identical" % len(paths))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
