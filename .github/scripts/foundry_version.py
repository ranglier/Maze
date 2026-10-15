"""Version du module Foundry (audit QUAL-8) : si backend/maze/foundry_module/ a changé depuis la base, la version
de module.json doit avoir augmenté (sinon Foundry ne propose pas la mise à jour aux mondes qui l'ont installé).

Usage : python .github/scripts/foundry_version.py <base>
  <base> : commit ou branche de comparaison (par exemple origin/main) ; on compare au point de divergence
  (git merge-base), comme une demande de fusion. Code 0 si rien n'a changé ou si la version a augmenté, 1 sinon.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

MODULE = "backend/maze/foundry_module"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def version_tuple(text: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(\.\d+)*", text):
        raise SystemExit(f"Version de module illisible : {text!r} (attendu : 1.2.3)")
    return tuple(int(p) for p in text.split("."))


def version_at(commit: str) -> str | None:
    try:
        return json.loads(git("show", f"{commit}:{MODULE}/module.json"))["version"]
    except subprocess.CalledProcessError:
        return None  # module absent à ce commit


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    base = git("merge-base", sys.argv[1], "HEAD").strip()
    changed = [f for f in git("diff", "--name-only", base, "HEAD", "--", MODULE).splitlines() if f]
    if not changed:
        print(f"Module Foundry inchangé depuis {base[:7]}.")
        return 0
    before, after = version_at(base), version_at("HEAD")
    print(f"Module Foundry modifié depuis {base[:7]} ({len(changed)} fichier(s)) : version {before} > {after}.")
    if after is None:
        print("module.json absent.")
        return 1
    if before is not None and version_tuple(after) <= version_tuple(before):
        print(f"ERREUR : augmenter « version » dans {MODULE}/module.json (actuellement {after}), sinon Foundry "
              "ne propose pas la mise à jour du module.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
