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
