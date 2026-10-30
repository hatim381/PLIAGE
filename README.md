# Assistant Pliage

## Installation et lancement (Windows PowerShell)

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Renseigner API_TOKEN dans .env.
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## Préparer les scans avant l’OCR

Dans le menu des pages, sélectionner **Préparer un PDF**. Cette page est accessible
sans token API. Importer un PDF, choisir chaque page tournée et appliquer une
rotation horaire de 90, 180 ou 270 degrés. Comparer les deux aperçus et télécharger
le PDF corrigé. Les pages non sélectionnées conservent leur orientation.

La détection automatique est facultative : installer Tesseract et ses données
`osd`, avec la commande `tesseract` disponible dans le PATH de la machine qui
exécute Streamlit. Le bouton propose une orientation par page. Les pages blanches
ou les détections de faible confiance ne changent pas. Vérifier les suggestions
visuellement. Sans Tesseract, les corrections manuelles restent disponibles.

Réimporter ensuite le PDF corrigé dans Pliage et relancer son indexation/OCR.
L’application ne transmet pas le fichier automatiquement et ne répare pas les
passages déjà indexés. Le PDF original reste intact. Le traitement conserve les
images et ajoute une rotation de page PDF, sans OCR ni recompression des images.
Le gain dépend de la prise en compte de cette rotation par l’outil OCR distant.

Limite : 100 pages par document. Le traitement s’effectue dans la mémoire et les
fichiers temporaires du serveur Streamlit. La correction d’orientation ne garantit
pas la restitution des cellules des tableaux. Vérifier les résultats sur plusieurs
documents, en particulier les colonnes associés/vacataires et les effectifs/taux.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Diagnostic des extractions

Chaque réponse multi-RAG présente un volet « Diagnostic des effectifs, protocoles
et IPA », indépendant du bouton d'affichage des preuves. Il contient les passages
intégraux reçus de Pliage, leur source et leur page lorsqu'elle existe, les citations,
les valeurs proposées et les motifs de rejet. Ces passages sont le texte reçu du
service, pas un nouvel OCR local. Le modèle peut recevoir une version tronquée
selon les plafonds du pipeline.

Les contrôles d'effectifs comparent la somme des catégories au total. Une ventilation
incomplète, ambiguë ou différente du total n'est pas validée. Les catégories numériques
à vérifier participent à la somme sans devenir confirmées. Aucun effectif manquant
n'est déduit. Un sous-total inférieur ne remet pas en cause un total directement attesté.

Les conditions de barème IPA, y compris « Sila structure » après OCR, ne confirment
pas une présence. Aucun point IPA ne prouve pas une absence. Les contrôles arithmétiques
ne certifient pas la lecture fidèle des tableaux.

## Relecture visuelle des tableaux

Dans la barre latérale, développer « Relire les tableaux en image », joindre le
PDF ciblé, indiquer jusqu'à cinq pages (par exemple `3,7`) et renseigner un identifiant
de modèle compatible images réellement accessible sur l'API Pliage. La variable
facultative `VISION_MODEL` préremplit cet identifiant. Activer la case puis envoyer
la recherche multi-RAG Premium. Aucun fournisseur externe ni clé supplémentaire
n'est utilisé : les images sont envoyées au même point d'entrée Pliage.

Le PDF joint doit correspondre au nom ciblé dans la question. Le service ne fournit
pas les images des documents déjà indexés : le fichier doit donc être joint dans
Streamlit. Le nom de fichier sert au contrôle de correspondance, pas à une vérification
d'identité du contenu. Vérifier le document joint. Les pages doivent être lisibles
et orientées avant la relecture.

La lecture visuelle ajoute une transcription distincte, numérotée V1, V2, etc.
Elle est affichée dans le diagnostic avec la page. Une divergence entre deux valeurs
attestées produit un conflit. Une valeur visuelle nouvelle ou opposée à un résultat
OCR ambigu reste à vérifier. Une transcription par modèle n'est pas une validation
humaine. Les erreurs d'API ou de modèle incompatible conservent le résultat OCR et
ajoutent un message de relecture indisponible. Le support effectif des images doit
être testé sur l'instance Pliage déployée.

Le contrôle des protocoles confronte aussi le nombre aux points de la même ligne
lorsqu'un tarif points/protocole est explicite dans le contexte reçu. Une incohérence
signale la cellule sans remplacer sa valeur par un calcul. Une égalité ne garantit
pas que l'OCR a lu le bon chiffre.
