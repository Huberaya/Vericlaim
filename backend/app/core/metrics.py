"""Métriques actionnables pour A-09, au format Prometheus.

A-09 constatait qu'aucune métrique n'existait : ``/readyz`` disait « prêt » ou
« pas prêt », et rien ne permettait de savoir *pourquoi* la file n'avançait
plus. Ce module expose les quatre signaux qui distinguent les causes :

* **API** — requêtes, erreurs 5xx, latence ;
* **base** — disponibilité de la sonde de connexion ;
* **files** — profondeur par état, âge du plus vieux job en attente ;
* **antivirus** — joignabilité et refus.

Le format est du texte Prometheus, volontairement sans dépendance : une
bibliothèque de métriques serait une dépendance de plus pour un besoin de
16 lignes. Aucun label ne contient de tenant, d'identifiant ou de nom de
fichier : une métrique n'est pas un journal.

Ce que ce module ne fait pas : il n'envoie d'alerte à personne. Les seuils
sont déclarés dans ``infra/monitoring/`` et c'est le collecteur qui décide.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


@dataclass
class _Counters:
    requests: int = 0
    server_errors: int = 0
    latency_sum: float = 0.0
    latency_count: int = 0
    latency_buckets: dict[float, int] = field(default_factory=lambda: defaultdict(int))


class MetricsRegistry:
    """Registre minimal, sûr entre threads.

    Les routes FastAPI s'exécutent dans un pool : un compteur non verrouillé
    perdrait des incréments, ce qui rendrait une alerte de taux d'erreur
    silencieusement fausse.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, str, str], _Counters] = defaultdict(_Counters)
        self._started_at = time.monotonic()
        self.scanner_reachable = 0
        self.scanner_checks = 0

    def observe_request(self, *, method: str, route: str, status_code: int, duration: float) -> None:
        # Le chemin est normalisé par l'appelant : jamais l'URL brute, qui
        # contiendrait des identifiants de documents.
        key = (method, route, str(status_code))
        with self._lock:
            counter = self._counters[key]
            counter.requests += 1
            if status_code >= 500:
                counter.server_errors += 1
            counter.latency_sum += duration
            counter.latency_count += 1
            for bound in _LATENCY_BUCKETS:
                if duration <= bound:
                    counter.latency_buckets[bound] += 1

    def observe_scanner(self, reachable: bool) -> None:
        with self._lock:
            self.scanner_checks += 1
            if reachable:
                self.scanner_reachable += 1

    def uptime_seconds(self) -> float:
        return time.monotonic() - self._started_at

    def render(self, extra: dict[str, str] | None = None) -> str:
        """Rend le registre au format texte Prometheus."""

        lines: list[str] = []
        lines.append("# HELP vericlaim_uptime_seconds Durée depuis le démarrage du processus.")
        lines.append("# TYPE vericlaim_uptime_seconds gauge")
        lines.append(f"vericlaim_uptime_seconds {self.uptime_seconds():.3f}")

        lines.append("# HELP vericlaim_http_requests_total Requêtes HTTP traitées.")
        lines.append("# TYPE vericlaim_http_requests_total counter")
        for (method, route, status_code), counter in sorted(self._counters.items()):
            labels = f'method="{method}",route="{route}",status="{status_code}"'
            lines.append(f"vericlaim_http_requests_total{{{labels}}} {counter.requests}")

        lines.append("# HELP vericlaim_http_request_duration_seconds Durée des requêtes.")
        lines.append("# TYPE vericlaim_http_request_duration_seconds histogram")
        for (method, route, status_code), counter in sorted(self._counters.items()):
            base = f'method="{method}",route="{route}",status="{status_code}"'
            for bound in _LATENCY_BUCKETS:
                lines.append(
                    f'vericlaim_http_request_duration_seconds_bucket{{{base},le="{bound}"}} '
                    f"{counter.latency_buckets[bound]}"
                )
            lines.append(f'vericlaim_http_request_duration_seconds_bucket{{{base},le="+Inf"}} {counter.latency_count}')
            lines.append(f"vericlaim_http_request_duration_seconds_sum{{{base}}} {counter.latency_sum:.6f}")
            lines.append(f"vericlaim_http_request_duration_seconds_count{{{base}}} {counter.latency_count}")

        lines.append("# HELP vericlaim_scanner_checks_total Sondes de joignabilité du moteur antivirus.")
        lines.append("# TYPE vericlaim_scanner_checks_total counter")
        lines.append(f"vericlaim_scanner_checks_total {self.scanner_checks}")
        lines.append("# HELP vericlaim_scanner_reachable_total Sondes ayant obtenu une réponse du moteur.")
        lines.append("# TYPE vericlaim_scanner_reachable_total counter")
        lines.append(f"vericlaim_scanner_reachable_total {self.scanner_reachable}")

        for name, value in (extra or {}).items():
            safe_name = "".join(character if character.isalnum() or character == "_" else "_" for character in name)
            lines.append(f"# TYPE vericlaim_{safe_name} gauge")
            lines.append(f"vericlaim_{safe_name} {value}")

        return "\n".join(lines) + "\n"


REGISTRY = MetricsRegistry()
