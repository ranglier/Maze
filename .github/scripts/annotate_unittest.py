"""Annotations GitHub Actions pour les tests en échec : une annotation « error » par FAIL / ERROR de la sortie de
unittest (en-tête du test et fin de la trace), visibles sur la page du run sans ouvrir le journal complet.

Usage : python .github/scripts/annotate_unittest.py <sortie de unittest>
"""

from __future__ import annotations

import re
import sys

SEPARATOR = re.compile(r"^={20,}$")
HEADER = re.compile(r"^(FAIL|ERROR): (.+)$")


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main() -> int:
    lines = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
    blocks: list[list[str]] = []
    for i, line in enumerate(lines):
        if SEPARATOR.match(line) and i + 1 < len(lines) and HEADER.match(lines[i + 1]):
            blocks.append([])
        elif blocks:
            blocks[-1].append(line)
    for block in blocks[:20]:
        kind, test = HEADER.match(block[0]).groups()
        body = [line for line in block[1:] if not re.match(r"^-{20,}$", line)]
        end = next((i for i, line in enumerate(body) if line.startswith("Ran ")), len(body))
        message = "\n".join(body[:end][-40:])[-3500:]
        print(f"::error title={escape(kind + ' ' + test)}::{escape(message)}")
    summary = [line for line in lines if line.startswith(("Ran ", "FAILED", "OK"))]
    if summary:
        print(f"::notice title=unittest::{escape(' '.join(summary[-2:]))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
