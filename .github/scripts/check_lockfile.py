"""Verrou des dépendances de l'interface (audit M21) : frontend/package-lock.json doit reprendre exactement les
versions de frontend/package.json (dépendances directes, racine du verrou) ; sinon npm ci échoue ou installe
autre chose que ce que le dépôt annonce.

Usage : python .github/scripts/check_lockfile.py [package.json] [package-lock.json]
        (défaut : frontend/package.json et frontend/package-lock.json)
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    pkg_path = sys.argv[1] if len(sys.argv) > 1 else "frontend/package.json"
    lock_path = sys.argv[2] if len(sys.argv) > 2 else "frontend/package-lock.json"
    pkg = json.load(open(pkg_path, encoding="utf-8"))
    lock = json.load(open(lock_path, encoding="utf-8"))
    bad = []
    if lock.get("lockfileVersion") not in (2, 3):
        bad.append(f"lockfileVersion {lock.get('lockfileVersion')} (attendu : 3, npm 9 et plus)")
    packages = lock.get("packages", {})
    root = packages.get("", {})
    wanted = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    for name, version in sorted(wanted.items()):
        got = packages.get(f"node_modules/{name}", {}).get("version")
        if got != version:
            bad.append(f"{name} : package.json {version}, verrou {got}")
    for key in ("dependencies", "devDependencies"):
        if root.get(key, {}) != pkg.get(key, {}):
            bad.append(f"{key} de la racine du verrou différent de package.json")
    unpinned = [f"{name} {spec}" for name, spec in wanted.items() if not spec[:1].isdigit()]
    if unpinned:
        bad.append(f"versions non exactes dans package.json : {', '.join(unpinned)}")
    missing = [p for p, meta in packages.items() if p and not meta.get("link")
               and not (meta.get("resolved") and meta.get("integrity"))]
    if missing:
        bad.append(f"paquets sans adresse ou sans empreinte : {', '.join(missing[:5])}")
    if bad:
        print("Verrou non conforme :\n  " + "\n  ".join(bad))
        return 1
    print(f"Verrou conforme : {len(wanted)} dépendances directes aux versions de package.json, "
          f"{len(packages) - 1} paquets avec empreinte (lockfileVersion {lock['lockfileVersion']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
