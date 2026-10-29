"""Familles de recherche et indicateurs ACI / MSP.

Aucune valeur ni aucun nom d'établissement n'est codé ici : uniquement
la description de ce qu'il faut chercher. `{target}` est remplacé par le
nom du fichier / de l'établissement ciblé, `{years}` par les années citées.
"""

from rag.schemas import Family, IndicatorDef

FAMILIES = (
    Family(
        key="professionnels",
        label="Professionnels",
        triggers=(
            r"\bps\b", "professionnel", "associe", "vacataire", "medecin",
            "paramedic", "pharmacien", "dentiste", "sage-femme", "sages-femmes",
            "signataire",
        ),
        keywords=("associe", "vacataire", "medecin", "paramedic", "professionnel", "signataire"),
        queries=(
            "professionnels associés signataires vacataires médecins généralistes paramédicaux pharmacien {target}",
            "nombre de professionnels de santé associés et vacataires {target}",
            "liste des professionnels signataires du contrat {target}",
        ),
    ),
    Family(
        key="patientele",
        label="Patientèle",
        triggers=("patientele", "file active", r"\bc2s\b", r"\bame\b", r"\bcmu"),
        keywords=("patientele", "file active", "c2s", "ame"),
        queries=(
            "patientèle de référence file active C2S AME {target}",
            "patientèle référence patients file active {target}",
            "nombre de patients C2S AME taux {target}",
        ),
    ),
    Family(
        key="remuneration",
        label="Rémunération ACI",
        triggers=("remuneration", "avance", "solde", "montant", "allouee", "verse"),
        keywords=("remuneration", "avance", "solde", "montant", "€", "euros"),
        queries=(
            "rémunération totale ACI avance solde exercice {years} {target}",
            "montant rémunération ACI avance versée solde {years} {target}",
            "total rémunération annuelle ACI {target}",
        ),
    ),
    Family(
        key="acces_soins",
        label="Accès aux soins",
        triggers=(
            "horaire", "ouverture", "amplitude", "non programme", r"\bsas\b",
            "acces aux soins",
        ),
        keywords=("horaire", "ouverture", "amplitude", "non programme", "sas", "samedi"),
        queries=(
            "horaires ouverture soins non programmés SAS accès aux soins {target}",
            "amplitude horaire ouverture samedi matin {target}",
            "service d'accès aux soins SAS participation {target}",
        ),
    ),
    Family(
        key="coordination",
        label="Coordination",
        triggers=("protocole", "coordination", "cooperation"),
        keywords=("protocole", "coordination", "cooperation"),
        queries=(
            "protocoles pluriprofessionnels réalisés rémunérés coordination {target}",
            "nombre de protocoles pluriprofessionnels {target}",
            "fonction de coordination rémunération {target}",
        ),
    ),
    Family(
        key="ipa_formation",
        label="IPA et formation",
        triggers=(r"\bipa\b", "pratique avancee", "stage", "formation", "jeunes", r"\binternes?\b"),
        keywords=("ipa", "pratique avancee", "stage", "formation", "jeune"),
        queries=(
            "IPA infirmier pratique avancée stages formation jeunes {target}",
            "accueil de stagiaires formation des jeunes professionnels {target}",
            "infirmier en pratique avancée {target}",
        ),
    ),
    Family(
        key="systeme_information",
        label="Système d'information",
        triggers=("systeme d'information", "systeme d information", r"\bans\b", "labellis", "logiciel"),
        keywords=("systeme d", "ans", "labellis", "logiciel", "niveau standard", "niveau avance"),
        queries=(
            "système information labellisé ANS niveau standard niveau avancé {target}",
            "système d'information partagé labellisation ANS {target}",
            "logiciel labellisé niveau standard points {target}",
        ),
    ),
)

FAMILIES_BY_KEY = {family.key: family for family in FAMILIES}


def _ind(key, label, family, kind, unit="", description="", optional=False, queries=()):
    return IndicatorDef(key, label, family, kind, unit, description, optional, queries)


# Requêtes de 2e passe : formulations courtes, au vocabulaire des fiches ACI,
# pour ramener les pages qu'une requête multi-thèmes ne remonte pas.
Q_ASSOCIES = ("professionnels associés signataires médecins généralistes professionnels paramédicaux pharmacien {target}",)
Q_PATIENTELE = ("Nombre de patients AME Taux de patients AME Nombre de patients C2S {target}",)
Q_REMUNERATION = (
    "Synthèse de la rémunération Avance sur exercice Solde sur exercice Total de la rémunération {target}",
    "votre rémunération au titre de l'exercice s'élève à avance au titre de l'exercice {target}",
)
Q_HORAIRES = ("Horaires d'ouverture amplitude horaire 8h 20h samedi matin {target}",)
Q_PROTOCOLES = (
    "2ème Axe Travail en équipe et coordination protocoles pluri-professionnels {target}",
    "Élaboration de protocoles pluri-professionnels nombre de protocoles {target}",
)
Q_IPA = ("Intégration d'un infirmier en pratique avancée IPA dans l'équipe {target}",)
Q_STAGES = (
    "Formation des jeunes professionnels de santé au moins 2 stages dans l'année {target}",
    "accueil de stagiaires maître de stage {target}",
)


INDICATORS = (
    # A — Professionnels
    _ind("associes_total", "Professionnels associés signataires (total)", "professionnels", "count", "professionnels",
         queries=Q_ASSOCIES),
    _ind("associes_medecins_generalistes", "Médecins généralistes associés", "professionnels", "count", "professionnels",
         queries=Q_ASSOCIES),
    _ind("associes_paramedicaux", "Paramédicaux associés", "professionnels", "count", "professionnels",
         queries=Q_ASSOCIES),
    _ind("associes_medecins_specialistes", "Médecins spécialistes associés", "professionnels", "count", "professionnels", optional=True),
    _ind("associes_chirurgiens_dentistes", "Chirurgiens-dentistes associés", "professionnels", "count", "professionnels", optional=True),
    _ind("associes_sages_femmes", "Sages-femmes associées", "professionnels", "count", "professionnels", optional=True),
    _ind("associes_pharmaciens", "Pharmaciens associés", "professionnels", "count", "professionnels", optional=True),
    _ind("associes_autres", "Autres professionnels associés", "professionnels", "count", "professionnels", optional=True),
    _ind("vacataires_total", "Professionnels vacataires (total)", "professionnels", "count", "professionnels"),
    _ind("vacataires_medecins", "Médecins vacataires", "professionnels", "count", "professionnels", optional=True),
    _ind("vacataires_paramedicaux", "Paramédicaux vacataires", "professionnels", "count", "professionnels", optional=True),
    _ind("vacataires_autres", "Autres professionnels vacataires", "professionnels", "count", "professionnels", optional=True),
    # B — Patientèle
    _ind("patientele_reference", "Patientèle de référence", "patientele", "count", "patients",
         "Patientèle réelle de la structure, jamais une patientèle de calcul "
         "(ex. « pour une patientèle de référence de N patients »)."),
    _ind("file_active", "File active", "patientele", "count", "patients"),
    _ind("c2s_patients", "Patients C2S", "patientele", "count", "patients"),
    _ind("c2s_taux", "Taux de patients C2S", "patientele", "percent", "%"),
    _ind("ame_patients", "Patients AME", "patientele", "count", "patients", queries=Q_PATIENTELE),
    _ind("ame_taux", "Taux de patients AME", "patientele", "percent", "%"),
    # C — Rémunération
    _ind("aci_remuneration_totale", "Rémunération totale ACI (exercice)", "remuneration", "money", "€",
         "Total de l'exercice : ni l'avance, ni le solde.", queries=Q_REMUNERATION),
    _ind("aci_avance_exercice", "Avance versée (exercice)", "remuneration", "money", "€", queries=Q_REMUNERATION),
    _ind("aci_solde_exercice", "Solde (exercice)", "remuneration", "money", "€", queries=Q_REMUNERATION),
    _ind("aci_avance_exercice_suivant", "Avance de l'exercice suivant", "remuneration", "money", "€", queries=Q_REMUNERATION),
    # D — Accès aux soins
    _ind("horaires_ouverture", "Horaires d'ouverture", "acces_soins", "text",
         description="Jours et heures d'ouverture de la structure, pas le barème de points.", queries=Q_HORAIRES),
    _ind("soins_non_programmes", "Soins non programmés chaque jour ouvré", "acces_soins", "text"),
    _ind("lien_sas", "Lien avec le SAS", "acces_soins", "text"),
    # E — Coordination
    _ind("protocoles_realises", "Protocoles pluriprofessionnels réalisés", "coordination", "count", "protocoles",
         "Nombre réalisé par la structure, pas le maximum possible.", queries=Q_PROTOCOLES),
    _ind("protocoles_remuneres", "Protocoles pluriprofessionnels rémunérés", "coordination", "count", "protocoles",
         queries=Q_PROTOCOLES),
    _ind("fonction_coordination", "Fonction de coordination", "coordination", "text", optional=True),
    # F — IPA / formation
    _ind("presence_ipa", "Présence d'IPA", "ipa_formation", "text", "",
         "Uniquement si le document l'indique explicitement pour la structure "
         "(ex. indicateur IPA atteint avec des points ou une rémunération).", queries=Q_IPA),
    _ind("stages_formation", "Stages (formation des jeunes)", "ipa_formation", "text", "",
         "Accueil de stagiaires par la structure : critère atteint (points ou rémunération "
         "obtenus) et formulation du document (ex. « au moins 2 stages dans l'année »).", queries=Q_STAGES),
    # G — Système d'information
    _ind("si_labellisation", "Système d'information labellisé ANS (niveau)", "systeme_information", "text", "",
         "Niveau obtenu par la structure (standard / avancé)."),
    _ind("si_points", "Points SI obtenus", "systeme_information", "points", "points", optional=True),
    _ind("si_remuneration", "Rémunération SI obtenue", "systeme_information", "money", "€", optional=True),
)

INDICATORS_BY_KEY = {ind.key: ind for ind in INDICATORS}


def indicators_for(families):
    wanted = set(families)
    return [ind for ind in INDICATORS if ind.family in wanted]
