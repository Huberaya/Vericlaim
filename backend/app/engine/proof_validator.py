"""Structured evidence checks. No client-provided `verified=true` is trusted.

The default certificate registry is empty. A production deployment can provide
an independently maintained, server-controlled registry snapshot through
VERICLAIM_CERTIFICATE_REGISTRY_JSON or inject another registry adapter.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from app.models.legal_types import (
    CarbonOffsetEvidence,
    ClaimType,
    EcolabelEvidence,
    EvidenceCheck,
    EvidenceDossier,
    EvidenceStatus,
    GHGInventoryEvidence,
    GHGReductionPlanEvidence,
    LcaEvidence,
    RecyclingRouteEvidence,
)
from app.models.schemas import AuditContext


@dataclass(frozen=True)
class RegistryRecord:
    scheme: str
    license_number: str
    valid_from: date
    valid_until: date | None
    product_identifiers: tuple[str, ...]
    product_categories: tuple[str, ...]
    relevant_claim_types: tuple[str, ...]
    issuer: str
    registry_name: str
    source_url: str | None = None
    officially_recognised: bool = False


class CertificateRegistry(Protocol):
    def find(self, license_number: str) -> RegistryRecord | None: ...


class NullCertificateRegistry:
    def find(self, license_number: str) -> RegistryRecord | None:
        return None


class JsonCertificateRegistry:
    """A deployment-owned snapshot; never populated from a request body."""

    def __init__(self, records: list[RegistryRecord] | None = None):
        self._records = {self._key(record.license_number): record for record in records or []}

    @staticmethod
    def _key(value: str) -> str:
        return re.sub(r"\s+", "", value).upper()

    @classmethod
    def from_json(cls, payload: str) -> "JsonCertificateRegistry":
        try:
            decoded = json.loads(payload or "{}")
        except json.JSONDecodeError:
            return cls([])
        raw_records = list(decoded.values()) if isinstance(decoded, dict) else decoded
        if not isinstance(raw_records, list):
            return cls([])
        records: list[RegistryRecord] = []
        for item in raw_records:
            if not isinstance(item, dict):
                continue
            try:
                records.append(
                    RegistryRecord(
                        scheme=str(item["scheme"]).upper(),
                        license_number=str(item["license_number"]),
                        valid_from=date.fromisoformat(str(item.get("valid_from", "2000-01-01"))),
                        valid_until=(
                            date.fromisoformat(str(item["valid_until"]))
                            if item.get("valid_until")
                            else None
                        ),
                        product_identifiers=tuple(str(x) for x in item.get("product_identifiers", [])),
                        product_categories=tuple(str(x) for x in item.get("product_categories", [])),
                        relevant_claim_types=tuple(str(x) for x in item.get("relevant_claim_types", [])),
                        issuer=str(item.get("issuer", "")),
                        registry_name=str(item.get("registry_name", "deployment certificate snapshot")),
                        source_url=item.get("source_url"),
                        officially_recognised=bool(item.get("officially_recognised", False)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                # Invalid server-side records are ignored rather than trusted.
                continue
        return cls(records)

    def find(self, license_number: str) -> RegistryRecord | None:
        return self._records.get(self._key(license_number))


class ProofValidator:
    def __init__(self, certificate_registry: CertificateRegistry | None = None):
        self.certificate_registry = certificate_registry or NullCertificateRegistry()

    @staticmethod
    def _evidence_id(item) -> str:
        return item.evidence_id or item.reference or item.file_name or "preuve-sans-identifiant"

    @staticmethod
    def _normalise_standard(value: str | None) -> str:
        return re.sub(r"[^a-z0-9]", "", (value or "").lower())

    def validate_lca(
        self,
        dossier: EvidenceDossier,
        context: AuditContext,
        *,
        comparative: bool = False,
    ) -> EvidenceCheck:
        items = [item for item in dossier.items if isinstance(item, LcaEvidence)]
        required = [
            "fichier ou référence du rapport",
            "mention ISO 14044",
            "périmètre produit",
            "unité fonctionnelle",
            "frontières du système",
        ]
        if comparative:
            required.extend(
                [
                    "au moins deux catégories d'impact",
                    "produit comparateur identifié",
                    "même unité fonctionnelle pour les deux produits",
                    "mêmes frontières du système",
                ]
            )
        if not items:
            return EvidenceCheck(
                check_name="ACV_ISO_14044",
                status=EvidenceStatus.NOT_PROVIDED,
                required_fields=required,
                missing_fields=required,
                detail="Aucun rapport d'ACV n'est déclaré dans le dossier.",
            )

        best_missing: list[str] | None = None
        best_item: LcaEvidence | None = None
        for item in items:
            missing: list[str] = []
            if not (item.file_name or item.reference):
                missing.append("fichier ou référence du rapport")
            standard = self._normalise_standard(item.standard)
            if "14044" not in standard:
                missing.append("mention ISO 14044")
            if not item.product_scope:
                missing.append("périmètre produit")
            if not item.functional_unit:
                missing.append("unité fonctionnelle")
            if not item.system_boundary:
                missing.append("frontières du système")
            if comparative:
                if not item.comparative:
                    missing.append("rapport explicitement comparatif")
                if len(set(item.impact_categories)) < 2:
                    missing.append("au moins deux catégories d'impact")
                if not item.comparison_product:
                    missing.append("produit comparateur identifié")
                if not item.same_functional_unit:
                    missing.append("même unité fonctionnelle pour les deux produits")
                if not item.same_system_boundary:
                    missing.append("mêmes frontières du système")
            if best_missing is None or len(missing) < len(best_missing):
                best_missing, best_item = missing, item
            if not missing:
                best_missing, best_item = [], item
                break

        best_missing = best_missing or []
        status = EvidenceStatus.METADATA_COMPLETE if not best_missing else EvidenceStatus.INCOMPLETE
        evidence_id = self._evidence_id(best_item) if best_item else ""
        detail = (
            "Les champs déclarés satisfont le seuil de métadonnées. Le moteur n'authentifie pas le fichier et n'a pas audité "
            "les choix de méthode, données d'inventaire, calculs, incertitudes ou conclusions."
            if not best_missing
            else "Le rapport déclaré ne contient pas toutes les métadonnées exigées par le contrôle."
        )
        if context.product_identifier and best_item and best_item.product_scope:
            detail += " La correspondance entre ce périmètre textuel et le SKU doit être confirmée par un réviseur."
        return EvidenceCheck(
            check_name="ACV_ISO_14044_COMPARATIVE" if comparative else "ACV_ISO_14044",
            status=status,
            evidence_ids=[evidence_id] if evidence_id else [],
            required_fields=required,
            missing_fields=best_missing,
            detail=detail,
            independently_verified=False,
        )

    def validate_ecolabel(
        self,
        dossier: EvidenceDossier,
        claim_type: ClaimType,
        context: AuditContext,
    ) -> EvidenceCheck:
        items = [item for item in dossier.items if isinstance(item, EcolabelEvidence)]
        required = [
            "numéro de licence présent",
            "licence trouvée dans le registre de confiance côté serveur",
            "validité à la date de l'allégation",
            "produit/catégorie et critère pertinents pour l'allégation",
        ]
        if not items:
            return EvidenceCheck(
                check_name="CERTIFICAT_ECOLABEL",
                status=EvidenceStatus.NOT_PROVIDED,
                required_fields=required,
                missing_fields=required,
                detail="Aucun certificat Ecolabel ou schéma Type I n'est déclaré.",
            )

        rejected: list[str] = []
        for item in items:
            record = self.certificate_registry.find(item.license_number)
            if record is None:
                rejected.append(self._evidence_id(item))
                continue
            if record.scheme != item.scheme:
                rejected.append(self._evidence_id(item))
                continue
            if context.as_of_date < record.valid_from or (record.valid_until and context.as_of_date > record.valid_until):
                rejected.append(self._evidence_id(item))
                continue
            if item.scheme == "EN_ISO_14024_TYPE_I" and not record.officially_recognised:
                rejected.append(self._evidence_id(item))
                continue
            if not (record.product_identifiers or record.product_categories):
                rejected.append(self._evidence_id(item))
                continue
            sku_match = bool(
                context.product_identifier
                and context.product_identifier in record.product_identifiers
            )
            category_match = bool(
                context.product_category
                and context.product_category in record.product_categories
            )
            evidence_scope_match = bool(
                item.product_identifier
                and item.product_identifier in record.product_identifiers
            ) or bool(
                item.product_category
                and item.product_category in record.product_categories
            )
            if not (sku_match or category_match or evidence_scope_match):
                rejected.append(self._evidence_id(item))
                continue
            relevant = {value.lower() for value in record.relevant_claim_types}
            if claim_type.value.lower() not in relevant and "generic_environmental" not in relevant:
                rejected.append(self._evidence_id(item))
                continue
            return EvidenceCheck(
                check_name="CERTIFICAT_ECOLABEL",
                status=EvidenceStatus.VERIFIED,
                evidence_ids=[self._evidence_id(item)],
                required_fields=required,
                missing_fields=[],
                detail=(
                    f"Licence retrouvée dans le registre serveur « {record.registry_name} »; "
                    f"émetteur déclaré: {record.issuer or item.issuer or 'non précisé'}. "
                    "La vérification porte sur la licence, le périmètre déclaré et la pertinence enregistrée, pas sur la véracité de chaque allégation marketing."
                ),
                independently_verified=True,
            )

        return EvidenceCheck(
            check_name="CERTIFICAT_ECOLABEL",
            status=EvidenceStatus.NOT_INDEPENDENTLY_VERIFIED,
            evidence_ids=[self._evidence_id(item) for item in items],
            required_fields=required,
            missing_fields=["validation indépendante du numéro, de la portée, de la validité et de la pertinence"],
            detail=(
                "Les numéros de licence fournis par le demandeur n'ont pas été corroborés par le registre serveur configuré "
                "ou ne correspondent pas au produit, à la date ou au type d'allégation. Ils ne déclenchent pas de Safe Harbor."
            ),
            independently_verified=False,
        )

    def validate_recycling_route(self, dossier: EvidenceDossier) -> EvidenceCheck:
        items = [item for item in dossier.items if isinstance(item, RecyclingRouteEvidence)]
        required = [
            "territoire(s) de vente / de collecte identifié(s)",
            "collecte effective disponible",
            "accès pratique du consommateur",
            "tri effectif vers une filière identifiée",
            "traitement industriel opérationnel",
        ]
        if not items:
            return EvidenceCheck(
                check_name="FILIERE_RECYCLAGE",
                status=EvidenceStatus.NOT_PROVIDED,
                required_fields=required,
                missing_fields=required,
                detail="Aucune preuve de filière de collecte, tri et traitement n'est déclarée.",
            )

        best_missing: list[str] | None = None
        best_item: RecyclingRouteEvidence | None = None
        for item in items:
            missing: list[str] = []
            if not item.territories:
                missing.append("territoire(s) de vente / de collecte identifié(s)")
            if not item.collection_available:
                missing.append("collecte effective disponible")
            if not item.consumer_access:
                missing.append("accès pratique du consommateur")
            if not item.sorting_available:
                missing.append("tri effectif vers une filière identifiée")
            if not item.industrial_processing_available:
                missing.append("traitement industriel opérationnel")
            if best_missing is None or len(missing) < len(best_missing):
                best_missing, best_item = missing, item
            if not missing:
                best_missing, best_item = [], item
                break
        best_missing = best_missing or []
        complete = not best_missing
        return EvidenceCheck(
            check_name="FILIERE_RECYCLAGE",
            status=EvidenceStatus.METADATA_COMPLETE if complete else EvidenceStatus.INCOMPLETE,
            evidence_ids=[self._evidence_id(best_item)] if best_item else [],
            required_fields=required,
            missing_fields=best_missing,
            detail=(
                "Les métadonnées de filière sont complètes pour au moins un territoire. Le moteur ne vérifie pas les contrats d'éco-organisme, "
                "les taux réels de collecte/tri ni la capacité de traitement."
                if complete
                else "La disponibilité de la filière n'est pas démontrée pour tous les champs requis."
            ),
            independently_verified=False,
        )

    def validate_french_carbon_neutrality(
        self,
        dossier: EvidenceDossier,
        *,
        offsetting_asserted: bool,
    ) -> list[EvidenceCheck]:
        inventories = [item for item in dossier.items if isinstance(item, GHGInventoryEvidence)]
        plans = [item for item in dossier.items if isinstance(item, GHGReductionPlanEvidence)]
        offsets = [item for item in dossier.items if isinstance(item, CarbonOffsetEvidence)]

        if not inventories:
            inventory_check = EvidenceCheck(
                check_name="BILAN_GES_PRODUIT",
                status=EvidenceStatus.NOT_PROVIDED,
                required_fields=["émissions directes", "émissions indirectes", "périmètre produit", "publication accessible au public"],
                missing_fields=["émissions directes", "émissions indirectes", "périmètre produit", "publication accessible au public"],
                detail="Aucun bilan GES du produit ou service n'est déclaré.",
            )
        else:
            inventory = inventories[0]
            missing = []
            if not inventory.includes_direct_emissions:
                missing.append("émissions directes")
            if not inventory.includes_indirect_emissions:
                missing.append("émissions indirectes")
            if not inventory.product_lifecycle_scope:
                missing.append("périmètre produit")
            if not (inventory.public_disclosure_url or inventory.reference):
                missing.append("publication accessible au public")
            inventory_check = EvidenceCheck(
                check_name="BILAN_GES_PRODUIT",
                status=EvidenceStatus.METADATA_COMPLETE if not missing else EvidenceStatus.INCOMPLETE,
                evidence_ids=[self._evidence_id(inventory)],
                required_fields=["émissions directes", "émissions indirectes", "périmètre produit", "publication accessible au public"],
                missing_fields=missing,
                detail="Métadonnées contrôlées uniquement; le contenu du bilan et ses calculs ne sont pas audités.",
            )

        if not plans:
            plan_check = EvidenceCheck(
                check_name="TRAJECTOIRE_EVIER_REDUIRE_COMPENSER",
                status=EvidenceStatus.NOT_PROVIDED,
                required_fields=["évitement prioritaire", "réduction avant compensation", "objectifs annuels quantifiés"],
                missing_fields=["évitement prioritaire", "réduction avant compensation", "objectifs annuels quantifiés"],
                detail="Aucune trajectoire d'évitement/réduction/compensation n'est déclarée.",
            )
        else:
            plan = plans[0]
            missing = []
            if not plan.avoidance_prioritised:
                missing.append("évitement prioritaire")
            if not plan.reduction_before_compensation:
                missing.append("réduction avant compensation")
            if not plan.annual_quantified_targets:
                missing.append("objectifs annuels quantifiés")
            if not (plan.public_disclosure_url or plan.reference):
                missing.append("publication accessible au public")
            plan_check = EvidenceCheck(
                check_name="TRAJECTOIRE_EVIER_REDUIRE_COMPENSER",
                status=EvidenceStatus.METADATA_COMPLETE if not missing else EvidenceStatus.INCOMPLETE,
                evidence_ids=[self._evidence_id(plan)],
                required_fields=["évitement prioritaire", "réduction avant compensation", "objectifs annuels quantifiés", "publication accessible au public"],
                missing_fields=missing,
                detail="Métadonnées contrôlées uniquement; les objectifs et leur réalisation ne sont pas validés.",
            )

        if offsetting_asserted and not offsets:
            offset_check = EvidenceCheck(
                check_name="COMPENSATION_RESIDUELLE",
                status=EvidenceStatus.NOT_PROVIDED,
                required_fields=["standard réglementaire", "preuve de retrait", "quantité", "émissions résiduelles couvertes"],
                missing_fields=["standard réglementaire", "preuve de retrait", "quantité", "émissions résiduelles couvertes"],
                detail="La communication évoque une compensation mais aucun justificatif de crédits n'est déclaré.",
            )
        elif offsetting_asserted:
            offset = offsets[0]
            missing = []
            if not offset.standard_or_registry:
                missing.append("standard réglementaire")
            if not offset.retirement_reference:
                missing.append("preuve de retrait")
            if offset.quantity_tco2e is None:
                missing.append("quantité")
            if not offset.residual_emissions_reference:
                missing.append("émissions résiduelles couvertes")
            offset_check = EvidenceCheck(
                check_name="COMPENSATION_RESIDUELLE",
                status=EvidenceStatus.METADATA_COMPLETE if not missing else EvidenceStatus.INCOMPLETE,
                evidence_ids=[self._evidence_id(offset)],
                required_fields=["standard réglementaire", "preuve de retrait", "quantité", "émissions résiduelles couvertes"],
                missing_fields=missing,
                detail="Métadonnées de crédits contrôlées; qualité, additionnalité, permanence et absence de double comptage non vérifiées.",
            )
        else:
            offset_check = EvidenceCheck(
                check_name="COMPENSATION_RESIDUELLE",
                status=EvidenceStatus.METADATA_COMPLETE,
                required_fields=["modalités de compensation des émissions résiduelles, le cas échéant"],
                missing_fields=[],
                detail="Aucun recours à la compensation n'est indiqué dans le texte ni dans le dossier; cette conclusion est à confirmer.",
            )

        return [inventory_check, plan_check, offset_check]
