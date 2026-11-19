"""Nommer chaque test en échec dans les annotations GitHub.

Un code de sortie ne dit pas *quoi* a cassé, et les journaux bruts d'un job ne
sont pas toujours récupérables. Ce script transforme un rapport JUnit en
annotations : chaque test rouge apparaît sur la demande de fusion, avec son
message, au lieu d'un « exit code 1 » anonyme.

Usage : ``python scripts/ci/annotate_junit.py /tmp/vericlaim-junit.xml``.
Sortie 0 si la suite est verte, 1 sinon.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def _escape(value: str) -> str:
    """Neutraliser ce que GitHub interprète dans une commande de workflow."""
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(",", "%2C")
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: annotate_junit.py <rapport-junit.xml>", file=sys.stderr)
        return 2

    root = ET.parse(argv[1]).getroot()
    total = failures = 0
    for case in root.iter("testcase"):
        total += 1
        problems = [node for node in case if node.tag in ("failure", "error")]
        if not problems:
            continue
        failures += 1
        for node in problems:
            message = (node.get("message") or node.text or "échec sans message").strip()
            message = " ".join(message.split())
            name = case.get("name") or "test inconnu"
            path = case.get("file") or "backend"
            print(
                f"::error file={_escape(path)},line=1,title={_escape(name)}::"
                f"{_escape(message)[:900]}"
            )

    if failures:
        print(f"{failures} test(s) sur {total} en échec.")
        return 1
    print(f"{total} test(s), aucun échec.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
