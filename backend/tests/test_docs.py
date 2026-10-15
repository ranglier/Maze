"""Documentation tenue à jour du code (vérification finale de l'audit, D3 et D5) : toutes les variables
d'environnement lues par le serveur figurent dans le tableau « Configuration » du README, et les tableaux du README
sont bien formés.

Lancer : cd backend && python -m unittest -q tests.test_docs
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "backend" / "maze"
# Lectures de l'environnement : os.environ.get("X"), os.environ["X"], os.getenv("X") et les aides de config.py
# (_env_secret, _env_bool, _env_int) et de db.py (_threads).
READ = re.compile(r"""(?:os\.environ(?:\.get)?\s*[\[(]|os\.getenv\(|\b_env_\w+\(|\b_threads\()\s*f?["']([A-Z][A-Z0-9_]*)["']""")


def server_variables() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        for name in READ.findall(path.read_text(encoding="utf-8")):
            found.setdefault(name, path.name)
    return found


def readme_section(title: str) -> str:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    start = text.index(f"\n## {title}\n")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


def table_variables(section: str) -> set[str]:
    names: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith("|---"):
            continue
        first = line.split("|")[1]
        for name in re.findall(r"[A-Z][A-Z0-9_]+(?:\(_FILE\))?", first):
            if name.endswith("(_FILE)"):
                name = name[:-len("(_FILE)")]
                names.add(name + "_FILE")
            names.add(name)
    return names


class EnvironmentDocTest(unittest.TestCase):
    def test_every_server_variable_in_configuration_table(self):
        found = server_variables()
        # Garde-fou du motif : les lectures connues sont bien trouvées.
        for name in ("MAZE_DATA_DIR", "MAZE_PASSWORD", "MAZE_LIVE_MAX_TOTAL", "MAZE_DB_READ_THREADS",
                     "MAZE_BACKUP_KEEP", "MAZE_MAX_IMPORT_MB", "MAZE_MAP_FONT"):
            self.assertIn(name, found)
        documented = table_variables(readme_section("Configuration"))
        missing = {name: where for name, where in found.items() if name not in documented}
        self.assertEqual(missing, {}, "variables lues par le serveur absentes du tableau Configuration du README")

    def test_variables_outside_the_server(self):
        """Lues par uvicorn (FORWARDED_ALLOW_IPS) ou par le service de mise à jour de l'hôte (MAZE_BRANCH)."""
        section = readme_section("Configuration")
        self.assertIn("FORWARDED_ALLOW_IPS", table_variables(section))
        self.assertIn('"${MAZE_BRANCH:-main}"', (REPO / "deploy" / "maze-updater.sh").read_text(encoding="utf-8"))
        self.assertIn("`MAZE_BRANCH` (default `main`)", section)


class ReadmeTablesTest(unittest.TestCase):
    def test_tables_well_formed(self):
        """D3 : un ancien en-tête resté au-dessus du nouveau cassait le tableau des sauvegardes sur GitHub. Chaque
        tableau : un en-tête, une ligne de séparation juste dessous, autant de colonnes à chaque ligne."""
        lines = (REPO / "README.md").read_text(encoding="utf-8").splitlines()
        tables: list[list[tuple[int, str]]] = []
        for number, line in enumerate(lines, 1):
            if line.startswith("|"):
                if not tables or tables[-1][-1][0] != number - 1:
                    tables.append([])
                tables[-1].append((number, line))
        self.assertGreater(len(tables), 3)

        def cells(line: str) -> int:
            return len(re.split(r"(?<!\\)\|", line.strip())) - 2
        for table in tables:
            (first, header), *rows = table
            separators = [n for n, line in table if re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", line.strip())]
            self.assertEqual(separators, [first + 1], f"README.md:{first} : ligne de séparation attendue sous l'en-tête "
                                                      "seulement")
            for number, line in rows:
                self.assertEqual(cells(line), cells(header), f"README.md:{number} : nombre de colonnes")


if __name__ == "__main__":
    unittest.main()
