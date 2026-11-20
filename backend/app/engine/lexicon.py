"""Versioned lexicon of environmental claim sensors.

Split out of :mod:`app.engine.fact_extractor` so the vocabulary can be reviewed,
versioned and measured independently of the code that walks sentences. It is still
purely lexical: a hit here is a *candidate assertion*, never a legal qualification.

C9 rewrote this lexicon after the audit measured that "100 % de matière recyclée"
was not detected and that certification claims (ECOLABEL, FSC, ISO 14001) were
recognised in no way whatsoever. Two design rules were kept from that measurement:

1. **Every pattern must be anchored.** Bare adjectives are the main source of false
   positives: "le vert est la couleur de notre logo" is not an environmental claim,
   and "arôme naturel de vanille" is a food descriptor. So "vert" and "naturel" only
   fire when attached to a product, a communication or a quantified form.
2. **An ambiguous word does not become a claim by accident.** "certifié conforme"
   is a legal conformity mark, not an environmental certification. The verb alone
   only fires with an environmental marker in the same sentence.

The corpus under ``tests/corpus/claim_detection_corpus.json`` measures both the
detection rate and the false-positive rate of this file.
"""

from __future__ import annotations

import re

# The version is part of the measurement: a corpus result is only meaningful for
# the lexicon revision it was produced with.
LEXICON_VERSION = "lexicon-2026-09-30.1"

NUMBER_WORD = r"\d+(?:[.,]\d+)?"

# --- Anchors ----------------------------------------------------------------- #

ENVIRONMENTAL_OBJECT = (
    r"(?:l['’]environnement|la\s+plan[eè]te|la\s+nature|le\s+climat|"
    r"l['’]impact\s+environnemental|le\s+vivant|l['’]air|l['’]eau|la\s+biodiversit[eé])"
)

# Nouns that make a bare adjective an assertion *about the offer*.
OFFER_NOUN = (
    r"(?:produit|produits|gamme|gammes|offre|offres|solution|solutions|"
    r"technologie|technologies|service|services|emballage|emballages|packaging|"
    r"flacon|flacons|bouteille|bouteilles|carton|cartons|sac|sacs|"
    r"communication|marketing|publicit[eé]|politique|engagement|engagements|"
    r"label|labels|certification|certifications|d[eé]marche|strat[eé]gie|"
    r"énergie|électricité|électrique|achat|achats|logistique|transport|"
    r"bâtiment|numérique|data|cloud|site|usine)"
)

# --- Claim families ---------------------------------------------------------- #

BIODEGRADABLE = re.compile(r"\b(?:bio)?d[eé]gradable(?:s)?\b", re.IGNORECASE)

NATURE_FRIENDLY = re.compile(
    r"\b(?:respectueu(?:x|se|ses)|respectueuses?)\s+de\s+l['’]environnement\b"
    r"|\brespectueu(?:x|se|ses)\s+de\s+la\s+plan[eè]te\b"
    r"|\b(?:ami|amie)s?\s+de\s+la\s+nature\b"
    r"|\b(?:bon|bonne|favorable)s?\s+(?:pour|à|a)\s+" + ENVIRONMENTAL_OBJECT + r"\b"
    r"|\b(?:pens(?:e|es|ent|er|ons)|agis(?:sons|sent)?|agit|agir|œuvr(?:e|es|ent|er|ons)|"
    r"travaill(?:e|es|ent|er|ons))\s+(?:pour|à|a)\s+" + ENVIRONMENTAL_OBJECT + r"\b"
    r"|\bprot[eè]g(?:e|es|ent|er|eons|ons)\s+" + ENVIRONMENTAL_OBJECT + r"\b"
    r"|\bpr[eé]serv(?:e|es|ent|er|ons)\s+" + ENVIRONMENTAL_OBJECT + r"\b"
    r"|\b(?:soucieu(?:x|se|ses)|pr[eé]occup[eé]e?s?)\s+de\s+" + ENVIRONMENTAL_OBJECT + r"\b"
    r"|\b(?:r[eé]duit|r[eé]duisons|limitons|diminuons)\s+(?:notre|son|notre)\s+impact\b"
    r"|\b[àa]\s+faible\s+impact\s+(?:environnemental|carbone|climatique)\b"
    r"|\bmoins\s+d['’]impact\s+sur\s+" + ENVIRONMENTAL_OBJECT + r"\b"
    r"|\bpour\s+un\s+avenir\s+(?:durable|plus\s+vert)\b"
    # "Nous faisons des efforts pour la planète" was the audit case F: vague, but a
    # public commitment about the environment, which the corpus expects to be seen.
    r"|\b(?:fais(?:ons|t|re)|men(?:ons|e|er)|fourniss(?:ons|ent)|investiss(?:ons|ent))\s+"
    r"(?:des\s+|de\s+nombreux\s+|d['’])?efforts?\s+(?:pour|en\s+faveur\s+d[eu])\s+" + ENVIRONMENTAL_OBJECT + r"\b"
    r"|\bengag[eé]e?s?\s+(?:pour|en\s+faveur\s+d[eu]|dans\s+la\s+protection\s+d[eu])\s+" + ENVIRONMENTAL_OBJECT + r"\b"
    # "engagé pour la protection de l'environnement": the object is embedded.
    r"|\bengag[eé]e?s?\s+(?:pour|à|a)\s+la\s+(?:protection|pr[eé]servation|d[eé]fense)\s+d[eu]\s+"
    r"(?:l['’]environnement|la\s+nature|la\s+plan[eè]te|le\s+climat|la\s+biodiversit[eé])\b"
    r"|\b(?:protection|pr[eé]servation|d[eé]fense)\s+d[eu]\s+(?:l['’]environnement|la\s+plan[eè]te|la\s+nature)\b"
    r"|\bnous\s+nous\s+engageons\s+(?:pour|à|a|en\s+faveur\s+d[eu])\s+" + ENVIRONMENTAL_OBJECT + r"\b"
    r"|\b(?:engagement|engagements|d[eé]marche|initiative)s?\s+(?:pour|en\s+faveur\s+d[eu])\s+" + ENVIRONMENTAL_OBJECT + r"\b"
    r"|\benvironmentally[- ]friendly\b|\bnature[- ]friendly\b|\bgentle\s+on\s+the\s+environment\b"
    r"|\bplan[eè]te[- ]friendly\b|\bclimate[- ]friendly\b",
    re.IGNORECASE,
)

CARBON_NEUTRALITY = re.compile(
    r"\bneutre\s+en\s+carbone\b"
    r"|\bneutralit[eé]\s+carbone\b"
    r"|\bz[eé]ro[- ]carbone\b"
    r"|\bempreinte\s+carbone\s+(?:est\s+)?(?:nulle|z[eé]ro)\b"
    r"|\bclimatiquement\s+neutre\b"
    r"|\bneutre\s+pour\s+le\s+climat\b"
    r"|\bimpact\s+climatique\s+(?:neutre|r[eé]duit|positif|n[eé]gatif)\b"
    r"|\b(?:carbon|climate)[- ](?:neutral|net[- ]zero|positive|negative|compensated)\b"
    r"|\bnet[- ]zero\b"
    r"|\bCO\s?2[- ]neutral\b"
    r"|\bcarbon\s+neutral\b"
    r"|\b(?:z[eé]ro|aucun)\s+impact\s+(?:carbone|climatique)\b"
    r"|\bsans\s+(?:aucun\s+)?impact\s+(?:sur\s+le\s+climat|climatique|carbone)\b"
    r"|\bbilan\s+carbone\s+positif\b"
    r"|\bcertifi[eé]e?\s+neutre\s+en\s+CO\s?2\b",
    re.IGNORECASE,
)

COMPARATIVE = re.compile(
    r"\b" + NUMBER_WORD + r"\s*(?:fois|x|times)\s+moins\s+(?:polluant|polluante|polluting|carbon[- ]intensive)\b"
    r"|\b(?:" + NUMBER_WORD + r"\s?%|" + NUMBER_WORD + r"\s+fois)\s+(?:moins|de\s+r[eé]duction)\b.{0,70}"
    r"\b(?:que|vs\.?|versus|compar[eé](?:e|s|es)?\s+[àa])\b"
    r"|\b(?:moins|plus)\s+(?:polluant(?:e|s|es)?|d['’]?[eé]missions|de\s+CO\s?2|carbon[- ]intensive)\b.{0,70}"
    r"\b(?:que|vs\.?|versus|compared\s+(?:to|with)|than)\b"
    r"|\b(?:twice|" + NUMBER_WORD + r"\s?x)\s+less\s+(?:polluting|pollutant|carbon[- ]intensive)\b"
    r"|\b(?:lower|less|reduced)\b.{0,60}\b(?:CO\s?2|carbon|emissions|environmental\s+impact)\b.{0,40}"
    r"\b(?:than|vs\.?|versus)\b",
    re.IGNORECASE,
)

# C9.1 — recycled *content*, not only recyclability. The audit's headline miss:
# "100 % de matière recyclée" produced no fact at all.
RECYCLABLE = re.compile(
    r"\brecycl(?:able|ables|abilit[eé])\b"
    r"|\brecycl[eé]e?s?\b"
    r"|\bmati[eè]res?\s+recycl[eé]e?s?\b"
    r"|\bcontient\s+(?:" + NUMBER_WORD + r"\s?%\s*(?:de\s+)?)?mati[eè]re\s+recycl[eé]e\b"
    r"|\b(?:fabriqu[eé]e?s?|confectionn[eé]e?s?|issu(?:e|s|es)?|compos[eé]e?s?)\s+"
    r"(?:à|a|en|avec|d[eu])\s+(?:mati[eè]res?|plastique|carton|papier|verre|aluminium|acier|fibres?|coton|PET|rPET)"
    r"\s+recycl[eé]e?s?\b"
    r"|\brecycled\b|\bpost[- ]consumer\s+(?:recycled\s+)?(?:content|material)\b"
    r"|\bmade\s+(?:from|with)\s+recycled\b|\brecycled\s+content\b",
    re.IGNORECASE,
)

# C9.2 — certification and label claims. Previously absent entirely: the product
# recognised no notion of a certificate, so the claim → evidence chain was empty
# for exactly the claims a buyer is most likely to rely on.
CERTIFICATION_SCHEME = re.compile(
    r"\b[eé]colabel(?:\s+europ[eé]en)?\b"
    r"|\bEU\s+Ecolabel\b|\bEU\s+ecolabel\b"
    r"|\bFSC\b|\bPEFC\b|\bSFI\b"
    r"|\bISO\s*14\s?\d{3}\b"
    r"|\bISO\s*140(?:01|06|21|24|40|44|64|67)\b"
    r"|\bNF\s+Environnement\b|\bNF\s+Certification\b"
    r"|\bB\s?Corp\b|\bBREEAM\b|(?<![A-Za-z])HQE(?![A-Za-z])"
    r"|\bCradle\s+to\s+Cradle\b|\bC2C\b"
    r"|\b[labelé]?\s*Agriculture\s+Biologique\b|\blabel\s+AB\b|\bBio\s+Equitable\b"
    # C23 — « biosourcé » n'est pas un schéma : c'est une **composition**. Le mot figure
    # dans la liste des marqueurs environnementaux (il qualifie bien une allégation), mais
    # le laisser ici faisait de la ligne « Contenu biosourcé : 12 % » une allégation de
    # certification dont le schéma était le mot lui-même. Mesuré par le corpus (cas FP27) :
    # il faut une preuve de certification (« certifié biosourcé »), pas un descriptif.
    r"|\bEcolabel\s+Europ[eé]en\b"
    r"|\bcertification\s+(?:environnementale|biologique|durable|climatique)\b"
    r"|\blabel\s+(?:environnemental|[eé]cologique|climatique|vert)\b"
    r"|\bsyst[eè]me\s+de\s+certification\b",
    re.IGNORECASE,
)

CERTIFICATION_VERB = re.compile(
    r"\bcertifi(?:[eé]e?s?|ation)\b|\bcertificat(?:s)?\b"
    r"|\blabellis(?:[eé]e?s?)\b|\b(?:certified|labelled)\b",
    re.IGNORECASE,
)

# "certifié conforme", "certifié CE", "certifié NF EN 71" are conformity or safety
# marks. Treating them as environmental certifications would be a false positive
# on a very common phrase.
CONFORMITY_MARK = re.compile(
    r"\bconforme\b|\bCE\b|\bNF\s+EN\s*\d+|\bqualit[eé]\b|\bISO\s*9001\b|\bs[eé]curit[eé]\b",
    re.IGNORECASE,
)

# The environmental marker that turns an ambiguous verb into a claim.
ENVIRONMENTAL_MARKER = re.compile(
    r"\b(?:environnement\w*|[eé]colog\w*|durable|durabilit[eé]|carbone|climat\w*|"
    r"[eé]mission\w*|recycl\w*|bio(?:logique|sourc\w*|d[eé]gradable)?|naturel\w*|"
    r"plan[eè]te|plastique|emballage\w*|papier|carton|verre|biodiversit[eé]|"
    r"[eé]nergie|[eé]lectricit[eé]|v[eé]g[eé]tal\w*|compost\w*|[eé]co[- ]?conception|CO\s?2)\b",
    re.IGNORECASE,
)

CERTIFICATE_NUMBER = re.compile(
    r"(?:n[°o]|num[eé]ro|no\.)\s*([A-Z0-9][A-Z0-9\-/\.]{3,})"
    # Registries also print the identifier between parentheses, e.g. "(FR/012/345)".
    r"|\(\s*([A-Z]{2}[/\-][0-9][A-Z0-9\-/\.]{3,})\s*\)",
    re.IGNORECASE,
)
CERTIFICATE_BODY = re.compile(
    r"(?:d[eé]livr[eé]e?s?|accord[eé]e?s?|obtenu(?:e|s|es)?|d[eé]cern[eé]e?s?)\s+"
    r"(?:par|aupr[eè]s\s+d[eu])\s+([A-ZÀ-Ý][\w\s\.\-'’]{2,48})"
    r"|organisme\s+(?:certificateur|certifiant)\s*:?\s*([A-ZÀ-Ý][\w\s\.\-'’]{2,48})"
    # "certifié FSC n° X par Ecocert": the body follows the verb, not a formula.
    r"|\b(?:certifi[eé]e?s?|labellis[eé]e?s?)\b[^.;!?]{0,48}?\bpar\s+([A-ZÀ-Ý][\w\s\.\-'’]{2,48})",
    re.IGNORECASE,
)

# C9.3 — generic claims. Bare adjectives are anchored to an offer noun; the
# unanchored forms are gone because they fired on colours, street names and food
# descriptors (measured in the corpus).
GENERIC_ENVIRONMENTAL = re.compile(
    # "éco" alone was removed: it matched the company name "Éco Logistique"
    # (corpus case FP12). The anchored forms below are kept.
    r"\b(?:[eé]cologique(?:s)?|[eé]co[- ]?(?:con[cç]u(?:e|s|es)?|responsable|friendly))\b"
    r"|\b" + OFFER_NOUN + r"\s+vert(?:e|s|es)?\b"
    r"|\bvert(?:e|s|es)?\s+" + OFFER_NOUN + r"\b"
    r"|\b100\s?%\s?(?:naturel|vert)\b"
    r"|\bproduit\s+100\s?%\s?naturel\b"
    r"|\bgreen\b|\bsustainable\b|\bclimate[- ]friendly\b|\bcarbon[- ]friendly\b"
    r"|\bconscious\b|\bnature['’]s\s+friend\b"
    r"|\b[eé]conomie\s+circulaire\b|\bd[eé]veloppement\s+durable\b"
    r"|\b[àa]\s+impact\s+positif\s+sur\s+" + ENVIRONMENTAL_OBJECT + r"\b",
    re.IGNORECASE,
)
