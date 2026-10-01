"""C23 — Lecture structurée des tableaux d'une fiche technique.

Avant ce module, un tableau ressortait en **texte brut** : la ligne
`Taux de matière recyclée | 62 | %` était rangée dans le même segment `paragraph` que
l'ensemble du bloc, et une allégation détectée dedans était citée « segment entier ».
Le modèle portait pourtant `SegmentType.TABLE` et `SegmentType.TABLE_CELL` depuis C4 :
l'énumération les annonçait, la chaîne d'extraction n'en produisait **jamais**.

Ce module fait trois choses, et rien d'autre :

1. il **repère** les lignes tabulaires (séparateur `|`, ou colonnes séparées par au moins
   deux espaces) et les regroupe en tableaux ;
2. il **type** les colonnes en `critère` / `valeur` / `unité` / `inconnu`, par l'en-tête
   quand il existe, par le contenu sinon ;
3. il **extrait** de chaque ligne une entrée `(critère, valeur, unité)` avec les offsets
   exacts de chaque cellule, pour qu'une allégation puisse être citée **cellule par
   cellule** au lieu du bloc entier.

Trois limites, écrites ici et publiées par l'API :

* le **rôle** d'une colonne est une heuristique de lecture, pas une interprétation : le
  produit ne conclut rien du fait qu'une cellule contient « 62 » ;
* une ligne n'est retenue comme tabulaire que si elle porte **au moins trois cellules** :
  deux colonnes séparées par un `|` isolé sont un texte courant ponctué, pas un tableau
  (mesuré : une phrase de prose avec un « | » donnait un faux tableau) ;
* un tableau d'**une seule ligne** n'est pas un tableau : il faut un en-tête et au moins
  une ligne de données, ou deux lignes de données.

Tout est déterministe : mêmes octets, même structure. Aucun modèle, aucun score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

#: Ce qu'une colonne contient, du point de vue de la lecture — pas du droit.
CELL_ROLES: tuple[str, ...] = ("criterion", "value", "unit", "unknown")

ROLE_LABELS: dict[str, str] = {
    "criterion": "critère",
    "value": "valeur",
    "unit": "unité",
    "unknown": "indéterminée",
}

#: Un séparateur de colonnes : la barre verticale, ou au moins deux espaces.
PIPE = re.compile(r"\s*\|\s*")
COLUMN_GAP = re.compile(r"\s{2,}")

#: Une cellule numérique : « 62 », « 62,5 », « 1 250 », « 62 % », « 12 kg ».
NUMERIC_CELL = re.compile(
    r"^\s*(?P<number>[-−]?\d{1,3}(?:[  \u202f]\d{3})*(?:[.,]\d+)?|[-−]?\d+(?:[.,]\d+)?)"
    r"\s*(?P<unit>[%‰a-zA-Z°µ][\w°%/²³.]*)?\s*$"
)

#: Une cellule qui n'est qu'une unité : « % », « kg CO2e », « t », « m² ».
#: Une cellule qui n'est qu'une unité. Un tiret, un « N/A » ou un « néant » n'en est pas
#: une : le produit publie « pas d'unité » plutôt qu'un caractère de ponctuation présenté
#: comme une unité de mesure.
UNIT_CELL = re.compile(
    r"^\s*(?:%|‰|kg(?:\s?CO\s?2\s?(?:e|eq)?)?|g(?:\s?CO\s?2\s?(?:e|eq)?)?|t(?:onnes?)?|"
    r"mg|ml|l|cl|dl|m[²2³3]?|cm|mm|km|kWh|MJ|°C|ppm|ans?|mois|jours?|h|min|s)"
    r"\s*$",
    re.IGNORECASE,
)

#: Cellules vides de sens : elles ne sont ni valeur, ni unité, ni critère.
EMPTY_MARKERS = frozenset({"-", "–", "—", "n/a", "na", "néant", "neant", "aucun", "aucune", "nc"})

HEADER_CRITERION = re.compile(
    r"\b(?:crit[eè]res?|param[eè]tres?|caract[eé]ristiques?|indicateurs?|propri[eé]t[eé]s?|"
    r"d[eé]signation|libell[eé]s?|postes?|rubriques?|[eé]l[eé]ments?|items?|d[eé]tails?)\b",
    re.IGNORECASE,
)
HEADER_VALUE = re.compile(
    r"\b(?:valeurs?|taux|r[eé]sultats?|mesures?|quantit[eé]s?|teneurs?|montants?|"
    r"pourcentages?|niveaux?|seuils?|objectifs?|valeurs?\s+mesur[eé]es?)\b",
    re.IGNORECASE,
)
HEADER_UNIT = re.compile(r"\b(?:unit[eé]s?|[uU]nit[eé]s?)\b", re.IGNORECASE)

#: Une ligne de tableau doit porter au moins trois cellules. Deux cellules séparées par
#: une barre verticale isolée sont une phrase ponctuée, et la prendre pour un tableau
#: faisait disparaître du texte courant de la segmentation par paragraphes.
MIN_CELLS_PER_ROW = 3

#: Un tableau doit porter au moins deux lignes cohérentes.
MIN_ROWS_PER_TABLE = 2


@dataclass(frozen=True)
class TableCell:
    """Une cellule, avec ses offsets **absolus** dans le texte canonique."""

    row_index: int
    column_index: int
    text: str
    start_offset: int
    end_offset: int
    role: str

    @property
    def is_numeric(self) -> bool:
        return bool(NUMERIC_CELL.match(self.text))


@dataclass(frozen=True)
class TableRow:
    index: int
    cells: tuple[TableCell, ...]
    start_offset: int
    end_offset: int
    is_header: bool = False


@dataclass(frozen=True)
class TableEntry:
    """Le contenu d'une ligne, lu comme (critère, valeur, unité)."""

    criterion: str | None
    criterion_offset: tuple[int, int] | None
    value: str | None
    value_offset: tuple[int, int] | None
    numeric_value: Decimal | None
    unit: str | None
    unit_offset: tuple[int, int] | None
    row_index: int

    def as_dict(self) -> dict[str, object]:
        return {
            "criterion": self.criterion,
            "criterion_offsets": list(self.criterion_offset) if self.criterion_offset else None,
            "value": self.value,
            "value_offsets": list(self.value_offset) if self.value_offset else None,
            "numeric_value": str(self.numeric_value) if self.numeric_value is not None else None,
            "unit": self.unit,
            "unit_offsets": list(self.unit_offset) if self.unit_offset else None,
            "row_index": self.row_index,
        }


@dataclass(frozen=True)
class StructuredTable:
    """Un tableau lu : sa page, ses colonnes typées, ses lignes et ses entrées."""

    page_number: int
    start_offset: int
    end_offset: int
    separator: str
    columns: tuple[str, ...]
    rows: tuple[TableRow, ...]
    entries: tuple[TableEntry, ...]
    has_header: bool

    @property
    def text(self) -> str:
        return ""

    def cell_for(self, row_index: int, column_index: int) -> TableCell | None:
        for row in self.rows:
            if row.index == row_index and column_index < len(row.cells):
                return row.cells[column_index]
        return None


def _split_line(line: str, *, line_offset: int) -> tuple[list[tuple[str, int, int]], str] | None:
    """Découpe une ligne en cellules, ou renvoie ``None`` si ce n'est pas tabulaire.

    Les offsets renvoyés sont relatifs au début du texte de la page.
    """

    if "|" in line:
        separator = "|"
        parts = PIPE.split(line)
        # Offsets : on avance dans la ligne en suivant les morceaux trouvés.
        cells: list[tuple[str, int, int]] = []
        cursor = 0
        for part in parts:
            found = line.find(part, cursor)
            if found < 0:  # pragma: no cover - défensif
                found = cursor
            cells.append((part, line_offset + found, line_offset + found + len(part)))
            cursor = found + len(part)
    else:
        separator = "columns"
        spans = list(COLUMN_GAP.finditer(line))
        if not spans:
            return None
        parts = COLUMN_GAP.split(line)
        cells = []
        cursor = 0
        for part in parts:
            found = line.find(part, cursor)
            if found < 0:  # pragma: no cover - défensif
                found = cursor
            cells.append((part, line_offset + found, line_offset + found + len(part)))
            cursor = found + len(part)

    trimmed = [(text.strip(), start, end) for text, start, end in cells]
    if len(trimmed) < MIN_CELLS_PER_ROW:
        return None
    return trimmed, separator


def _looks_like_header(cells: list[tuple[str, int, int]]) -> bool:
    """Un en-tête porte des mots de colonne et aucun nombre."""

    if any(NUMERIC_CELL.match(text) and not UNIT_CELL.match(text) for text, _, _ in cells):
        return False
    words = sum(1 for text, _, _ in cells if re.search(r"[A-Za-zÀ-ÿ]{3,}", text))
    return words >= max(2, len(cells) - 1)


def _role_for_column(header: str | None, values: list[str]) -> str:
    """Le rôle d'une colonne : l'en-tête d'abord, le contenu ensuite."""

    if header:
        if HEADER_UNIT.search(header) and not HEADER_VALUE.search(header):
            return "unit"
        if HEADER_CRITERION.search(header):
            return "criterion"
        if HEADER_VALUE.search(header):
            return "value"
    if not values:
        return "unknown"
    unit_cells = sum(1 for value in values if UNIT_CELL.match(value))
    numeric_cells = sum(1 for value in values if NUMERIC_CELL.match(value))
    word_cells = sum(1 for value in values if re.search(r"[A-Za-zÀ-ÿ]{4,}", value))
    total = len(values)
    if unit_cells >= max(1, total * 0.6):
        return "unit"
    if numeric_cells >= max(1, total * 0.6):
        return "value"
    if word_cells >= max(1, total * 0.6):
        return "criterion"
    return "unknown"


def _numeric(text: str | None) -> tuple[Decimal | None, str | None]:
    if not text:
        return None, None
    match = NUMERIC_CELL.match(text)
    if match is None:
        return None, None
    raw = match.group("number").replace("\u202f", "").replace(" ", "").replace(",", ".")
    try:
        value = Decimal(raw)
    except InvalidOperation:  # pragma: no cover - le motif garantit un nombre
        return None, None
    return value, (match.group("unit") or None)


def _entry_for_row(row: TableRow) -> TableEntry | None:
    """Lire une ligne comme (critère, valeur, unité), sans rien inventer."""

    criterion = None
    criterion_offset = None
    value = None
    value_offset = None
    unit = None
    unit_offset = None

    for cell in row.cells:
        if cell.text.strip().casefold() in EMPTY_MARKERS:
            continue
        if cell.role == "criterion" and criterion is None and cell.text:
            criterion, criterion_offset = cell.text, (cell.start_offset, cell.end_offset)
        elif cell.role == "value" and value is None and cell.text:
            value, value_offset = cell.text, (cell.start_offset, cell.end_offset)
        elif cell.role == "unit" and unit is None and cell.text:
            unit, unit_offset = cell.text, (cell.start_offset, cell.end_offset)

    # Une valeur peut être rangée dans une colonne « inconnue » (fiche sans en-tête
    # reconnu) : on la prend alors dans la première cellule numérique disponible, et
    # l'unité dans la première cellule qui n'est qu'une unité. Ce qui n'est pas trouvé
    # reste `None` : le produit ne complète pas un tableau par déduction.
    if value is None:
        for cell in row.cells:
            if cell.role in {"unknown", "criterion"} and cell.is_numeric:
                value, value_offset = cell.text, (cell.start_offset, cell.end_offset)
                break
    if unit is None:
        for cell in row.cells:
            if cell.text.strip().casefold() in EMPTY_MARKERS:
                continue
            if UNIT_CELL.match(cell.text):
                unit, unit_offset = cell.text, (cell.start_offset, cell.end_offset)
                break

    numeric, inline_unit = _numeric(value)
    if unit is None and inline_unit:
        unit = inline_unit

    if criterion is None and value is None and unit is None:
        return None
    return TableEntry(
        criterion=criterion,
        criterion_offset=criterion_offset,
        value=value,
        value_offset=value_offset,
        numeric_value=numeric,
        unit=unit,
        unit_offset=unit_offset,
        row_index=row.index,
    )


def parse_page_tables(page_text: str, *, page_number: int, page_offset: int = 0) -> tuple[StructuredTable, ...]:
    """Repérer les tableaux d'une page et les structurer.

    ``page_offset`` est la position du début de la page dans le texte canonique :
    toutes les cellules renvoyées portent des offsets valides dans ce texte, pour que
    la citation d'une allégation désigne la bonne cellule.
    """

    if not page_text:
        return ()

    lines: list[tuple[int, int, str]] = []
    cursor = 0
    for raw_line in page_text.split("\n"):
        lines.append((cursor, cursor + len(raw_line), raw_line))
        cursor += len(raw_line) + 1

    parsed: list[tuple[int, int, str, list[tuple[str, int, int]]]] = []
    for start, end, line in lines:
        split = _split_line(line, line_offset=page_offset + start)
        if split is None:
            parsed.append((start, end, line, []))
            continue
        cell_list, separator = split
        parsed.append((start, end, separator, cell_list))

    tables: list[StructuredTable] = []
    index = 0
    while index < len(parsed):
        start, end, separator, cells = parsed[index]
        if not cells:
            index += 1
            continue
        block: list[tuple[int, int, list[tuple[str, int, int]]]] = [(start, end, cells)]
        probe = index + 1
        while probe < len(parsed):
            n_start, n_end, n_separator, n_cells = parsed[probe]
            if not n_cells or len(n_cells) != len(cells):
                break
            if n_separator != separator:
                break
            block.append((n_start, n_end, n_cells))
            probe += 1

        if len(block) >= MIN_ROWS_PER_TABLE:
            header_cells = block[0][2]
            has_header = _looks_like_header(header_cells)
            header_texts = [text for text, _, _ in header_cells] if has_header else [None] * len(cells)
            data_rows = block[1:] if has_header else block
            values_by_column: list[list[str]] = [[] for _ in range(len(cells))]
            for _, _, row_cells in data_rows:
                for position, (text, _, _) in enumerate(row_cells):
                    values_by_column[position].append(text)
            roles = tuple(
                _role_for_column(header_texts[position], values_by_column[position])
                for position in range(len(cells))
            )

            rows: list[TableRow] = []
            for row_index, (row_start, row_end, row_cells) in enumerate(block):
                is_header = has_header and row_index == 0
                role_for_row = ("unknown",) * len(cells) if is_header else roles
                rows.append(
                    TableRow(
                        index=row_index,
                        cells=tuple(
                            TableCell(
                                row_index=row_index,
                                column_index=column_index,
                                text=text,
                                start_offset=cell_start,
                                end_offset=cell_end,
                                role=role_for_row[column_index],
                            )
                            for column_index, (text, cell_start, cell_end) in enumerate(row_cells)
                        ),
                        start_offset=page_offset + row_start,
                        end_offset=page_offset + row_end,
                        is_header=is_header,
                    )
                )
            entries = tuple(
                entry for entry in (_entry_for_row(row) for row in rows if not row.is_header) if entry is not None
            )
            tables.append(
                StructuredTable(
                    page_number=page_number,
                    start_offset=page_offset + block[0][0],
                    end_offset=page_offset + block[-1][1],
                    separator=separator,
                    columns=roles,
                    rows=tuple(rows),
                    entries=entries,
                    has_header=has_header,
                )
            )
        index = probe

    return tuple(tables)


def table_blocks_in_text(text: str, *, page_number: int, page_offset: int = 0) -> tuple[tuple[int, int], ...]:
    """Les plages **absolues** occupées par un tableau, dans l'ordre du document.

    Sert à retirer ces lignes de la segmentation par paragraphes : sans cela, les
    cellules seraient à la fois un tableau structuré et un paragraphe, et la même
    allégation aurait deux citations concurrentes.
    """

    return tuple(
        (table.start_offset, table.end_offset)
        for table in parse_page_tables(page_text=text, page_number=page_number, page_offset=page_offset)
    )


def find_entries(
    tables: tuple[StructuredTable, ...],
    *,
    criterion_keywords: tuple[str, ...],
) -> tuple[TableEntry, ...]:
    """Les entrées dont le critère porte l'un des mots-clés, sans interprétation.

    Renvoie les entrées dans l'ordre du document. Le produit ne conclut rien : il rend
    une valeur déclarée dans un tableau, et l'appelant décide ce qu'il en dit.
    """

    needles = tuple(keyword.casefold() for keyword in criterion_keywords if keyword)
    if not needles:
        return ()
    found: list[TableEntry] = []
    for table in tables:
        for entry in table.entries:
            if entry.criterion and any(needle in entry.criterion.casefold() for needle in needles):
                found.append(entry)
    return tuple(found)


__all__ = [
    "CELL_ROLES",
    "ROLE_LABELS",
    "StructuredTable",
    "TableEntry",
    "TableRow",
    "TableCell",
    "find_entries",
    "parse_page_tables",
    "table_blocks_in_text",
]
