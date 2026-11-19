# Procédure d'exercice des droits — opérationnelle

**Ce document décrit une procédure implémentée, pas une intention.** Chaque étape cite
la route, le fichier et le test correspondant. Les délais sont **calculés par le
système**, jamais saisis.

## 1. Comment une demande est reçue

Il n'y a pas de formulaire public : une demande arrive par le canal que le client publie
(adresse de contact, courriel du DPO). Elle est **enregistrée dans le produit**, sinon
elle n'existe pas et aucun délai ne court de façon vérifiable.

```bash
# Réception d'une demande d'accès (permission organization:manage + jeton CSRF)
curl -sS -b cookies.txt -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -X POST https://<api>/api/v1/data-rights \
  -d '{
        "request_type": "access",
        "requester_email": "personne@example.org",
        "requester_name": "Camille Martin",
        "details": "Je demande une copie des données me concernant.",
        "received_at": "2026-09-12T09:30:00+02:00"
      }'
```

* `received_at` est la date de **réception réelle** (courrier, courriel) : le délai court
  à partir d'elle, pas de la saisie. Omise, elle vaut l'instant de l'enregistrement.
* `due_at` est **calculé** : `received_at` + un mois calendaire (art. 12.3), avec gestion
  des fins de mois (31 janvier → 28/29 février).
* L'événement `privacy.rights_request_received` est écrit dans la chaîne d'audit.

## 2. Suivi et délais

```bash
# Tableau de bord : combien de demandes ouvertes, combien en retard
curl -sS -b cookies.txt https://<api>/api/v1/data-rights

# Ce que le produit sait réellement traiter, droit par droit (publié)
curl -sS https://<api>/api/v1/data-rights/procedure
```

La réponse de liste publie `open`, `overdue` et, pour chaque demande, `days_remaining`
et `overdue`. Une demande en retard est donc **visible dans l'API**, pas découverte
lors d'un contrôle.

**Prolongation** (art. 12.3, seconde phrase) : deux mois au maximum, **motivés et
écrits**, une seule fois, et sans effacer l'échéance d'origine (qui reste publiée) :

```bash
curl -sS -b cookies.txt -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -X POST https://<api>/api/v1/data-rights/<id>/extend \
  -d '{"reason": "Volume important et demandes complexes : le délai de deux mois est appliqué."}'
```

## 3. Exécution, droit par droit

| Droit | Outillé ? | Comment | Preuve |
| --- | --- | --- | --- |
| Accès (art. 15) | **oui** | `GET /api/v1/privacy/export` — export complet de l'organisation | L'export porte `export_metadata.manifest_sha256`, empreinte **recalculable par le destinataire** (voir § 3.1) |
| Portabilité (art. 20) | **oui, avec une réserve** | Même export, en JSON structuré | Réserve publiée : pas de format dédié interopérable |
| Effacement (art. 17) | **oui** | `POST /api/v1/privacy/erasures` (document ou analyse) | `manifest_sha256` du manifeste d'effacement ; refus **409** motivé si un gel légal protège le contenu |
| Rectification (art. 16) | **non, manuel** | Nouvelle version d'analyse (revue humaine) ou remplacement de la version de document | La demande est enregistrée et suivie ; le parcours dédié n'existe pas |
| Restriction (art. 18) | **non** | Le gel légal empêche la suppression, il ne limite pas le traitement | Publié comme non outillé |
| Opposition (art. 21) | **non** | Se traite contractuellement, et conduit en pratique à un effacement, lui outillé | Publié comme non outillé |

### 3.1 Vérifier l'empreinte remise au demandeur

L'empreinte est calculée sur le contenu **sérialisé**, métadonnée d'empreinte vidée. Le
destinataire peut donc la recalculer lui-même, sans faire confiance à l'expéditeur :

```bash
curl -sS -b cookies.txt https://<api>/api/v1/privacy/export -o export.json
python3 -c '
import hashlib, json
export = json.load(open("export.json"))
published = export["export_metadata"]["manifest_sha256"]
export["export_metadata"]["manifest_sha256"] = ""
recomputed = hashlib.sha256(
    json.dumps(export, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
print("publiée   :", published)
print("recalculée:", recomputed)
print("identiques" if published == recomputed else "DIFFÉRENTES — ne pas accepter")
'
```

**Défaut réel corrigé pendant C12** : au premier essai, cette comparaison échouait —
l'empreinte était calculée sur le dictionnaire Python, où `str(datetime)` s'écrit
`2026-09-30 19:15:55+00:00`, alors que le JSON reçu porte `2026-09-30T19:15:55+00:00`.
Une référence que seul l'émetteur peut recalculer n'est pas une preuve : elle a été
publiée comme telle pendant C20 sans que rien ne le détecte. Le test
`test_the_export_reference_can_be_recomputed_by_whoever_receives_it` recalcule
désormais de l'extérieur, et la mutation correspondante est détectée par
`audit/c12_mutations.py`.

## 4. Clôture : ce qui a été remis, ou pourquoi c'est refusé

```bash
# Accord : la référence de ce qui a été remis est OBLIGATOIRE
curl -sS -b cookies.txt -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -X POST https://<api>/api/v1/data-rights/<id>/close \
  -d '{"outcome": "granted",
       "outcome_reference": "<manifest_sha256 de l export>",
       "outcome_detail": "Export complet transmis au demandeur."}'

# Refus : le motif écrit est OBLIGATOIRE
curl -sS -b cookies.txt -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -X POST https://<api>/api/v1/data-rights/<id>/close \
  -d '{"outcome": "refused",
       "refusal_reason": "Le demandeur n'est pas la personne concernée."}'
```

Un accord **sans référence** est refusé (400, et contrainte en base) : « nous avons
répondu » sans dire quoi est invérifiable. Un refus **sans motif** est refusé de même.
La clôture écrit `privacy.rights_request_closed` avec le retard éventuel.

## 5. Ce que la procédure ne fait pas (limites publiées)

- **Pas de formulaire public** : la réception dépend du canal publié par le client.
- **Rectification, restriction et opposition non outillées** : enregistrées, suivies,
  traitées à la main. Un test vérifie que la carte publiée ne prétend pas le contraire.
- **Pas de notification automatique** au demandeur : la réponse est envoyée par le
  support ; l'API ne l'envoie pas.
- **Pas d'escalade automatique** à l'approche de l'échéance : le retard est visible dans
  la liste, personne n'est alerté automatiquement aujourd'hui.

## 6. Où c'est testé

`backend/tests/test_data_subject_rights.py` — 18 tests : délai calculé (dont les fins de
mois et le cas bissextile), accord sans preuve refusé, refus sans motif refusé,
prolongation motivée et unique, frontière de locataire, permission et jeton CSRF exigés,
et deux tests qui vérifient que le parcours annoncé **existe vraiment** (routes réelles,
export et effacement produisant la référence de clôture).
