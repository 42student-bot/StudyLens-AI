# StudyLens AI — backend Messenger

Assistant documentaire pour Meta Ray-Ban Display, **sans application à installer** : vous envoyez
vos photos de feuilles depuis les lunettes vers une Page Facebook (Messenger, natif sur les lunettes) ;
ce backend analyse toutes les pages ensemble et renvoie une question + sa réponse par message court.

## Statut honnête

| Élément | État |
|---|---|
| Logique (sessions, multi-pages, flou, secours IA, budget, vérification des calculs, découpage des messages, signature des webhooks, anti-SSRF) | **Testé automatiquement** (68 tests, voir ci-dessous) |
| Serveur HTTP (handshake Meta, signature, 200 immédiat, idempotence) | **Testé en local** avec de vraies requêtes signées |
| Appels réels à Gemini / Anthropic | Format de requête testé avec un faux serveur ; **jamais exécuté avec une vraie clé** |
| Échange réel avec Meta (Messenger Platform) | **Non testé** : il faut votre app, votre Page et un domaine HTTPS |
| Photo envoyée depuis les lunettes → Page, lecture de la réponse sur l'écran, nombre de gestes | **Non testé** : à vérifier sur votre matériel (voir « Test à faire en premier ») |
| Qualité des réponses de l'IA sur vos feuilles | **Non mesurée** : utilisez `scripts.analyze_files` |

## Fonctionnement

1. Vous photographiez une feuille (geste du Neural Band) et l'envoyez à la Page, plusieurs fois de suite.
2. Chaque photo reçoit « Page N capturée ». Une photo floue est refusée (« ⚠ Photo floue : reprenez la page N ») et **n'est pas comptée**.
3. **25 s sans nouvelle photo** (`AUTO_ANALYZE_DELAY`) : l'analyse démarre seule — aucun clavier, aucune voix.
4. Toutes les pages partent **dans un seul appel** au modèle multimodal → JSON (transcription, questions, réponses, source, origine document/général, confiance).
5. Les calculs annoncés sont **recalculés** par un évaluateur sûr ; une relecture texte-seule corrige les erreurs.
6. Résultats : un message « N questions » puis `Q1/8`, `Q2/8`… (réponses longues découpées).
7. Les images sont supprimées après l'analyse ; les résultats après 48 h.

Commandes texte **facultatives** (utiles seulement si l'écriture silencieuse est disponible chez vous) :
`fin` · `annule` (dernière page) · `supprime` (tout) · `q3` (revoir la question 3) · `aide`.

```
Lunettes ──photo──► Messenger ──webhook HTTPS──► Caddy ─► uvicorn/Starlette (app/main.py)
                                                           │ signature HMAC vérifiée, 200 immédiat
                                                           ▼
                          orchestrator.py  (sessions, minuteur, commandes, reprises)
                           ├─ messenger.py  (téléchargement sécurisé, envoi)
                           ├─ imaging.py    (orientation, redimensionnement, flou)
                           ├─ pipeline.py   (IA multimodale → JSON → vérifs)  ── ai/ (Gemini, Anthropic, secours)
                           ├─ budget.py     (plafonds jour/mois, requêtes/jour)
                           └─ store.py      (SQLite + fichiers, purge automatique)
```

## Installation locale (Windows, PowerShell)

```powershell
cd studylens-backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest              # doit afficher 68 passed
```

### Juger l'IA sur vos vraies feuilles (sans lunettes ni Messenger)

```powershell
$env:PRIMARY_PROVIDER="gemini"
$env:GEMINI_API_KEY="votre_clé"
$env:GEMINI_FREE_TIER="true"      # seulement après avoir vérifié le quota gratuit
python -m scripts.analyze_files page1.jpg page2.jpg page3.jpg
```

Affiche le score de netteté de chaque photo (pour calibrer `BLUR_THRESHOLD`), les questions, les réponses
et le rendu exact des messages. Avec `PRIMARY_PROVIDER=mock`, les réponses sont factices (test de la chaîne uniquement).

## Configurer Meta

Les intitulés de l'interface Meta évoluent : suivez la documentation Messenger Platform en cas d'écart.

1. https://developers.facebook.com → **Créer une app** (cas d'usage Messenger / type Business) et ajouter le produit **Messenger**.
2. Utiliser une **Page Facebook** (la créer au besoin) et **générer un jeton d'accès de Page** → `FB_PAGE_TOKEN`.
3. Paramètres de l'app → Général → **Clé secrète** → `FB_APP_SECRET`.
4. Messenger → Webhooks : URL de rappel `https://VOTRE_DOMAINE/webhook`, jeton de vérification = `FB_VERIFY_TOKEN`,
   abonner la Page au champ **messages**.
5. Tant que l'app est en mode développement, seuls les comptes ayant un rôle sur l'app (vous) peuvent écrire à la Page.
   C'est suffisant pour un usage personnel ; ne demandez pas de validation d'app.
6. Écrivez « bonjour » à la Page depuis Messenger : la réponse sera « Accès non autorisé » et le journal contiendra
   `expéditeur non autorisé psid=XXXX`. Copiez XXXX dans `ALLOWED_PSIDS` et redémarrez. **Sans cela, personne n'est autorisé.**

`GRAPH_API_VERSION` peut rester vide (URL non versionnée) ; fixez-la plutôt si Meta vous le demande.

## Déployer sur la VM Debian 13 (e2-micro)

Prérequis : un **nom de domaine** pointant vers l'IP publique de la VM (Meta exige du HTTPS valide) et les ports 80/443
ouverts dans le pare-feu Google Cloud. Le backend ne fait tourner aucun modèle : il orchestre et appelle les API,
ce qui convient à 1 Go de RAM.

```bash
sudo apt update && sudo apt install -y python3-venv caddy
sudo useradd --system --home /opt/studylens --shell /usr/sbin/nologin studylens
sudo mkdir -p /opt/studylens /etc/studylens
# copiez le projet dans /opt/studylens (git clone depuis votre GitHub, ou gcloud compute scp)
sudo python3 -m venv /opt/studylens/.venv
sudo /opt/studylens/.venv/bin/pip install -r /opt/studylens/requirements.txt
sudo chown -R studylens:studylens /opt/studylens

sudo cp /opt/studylens/.env.example /etc/studylens/env && sudo chmod 600 /etc/studylens/env
sudo nano /etc/studylens/env        # remplir les clés (jamais dans GitHub)

sudo cp /opt/studylens/deploy/studylens.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now studylens
sudo cp /opt/studylens/deploy/Caddyfile /etc/caddy/Caddyfile   # éditer le domaine
sudo systemctl reload caddy

curl https://VOTRE_DOMAINE/healthz          # {"status":"ok","providers":[...]}
sudo journalctl -u studylens -f             # journaux (aucun contenu de document n'y figure)
```

Mise à jour : remplacer les fichiers, `pip install -r requirements.txt`, `sudo systemctl restart studylens`.
Le coût de la VM dépend de votre éligibilité à l'offre gratuite Google Cloud : vérifiez-le dans la console de facturation et
créez une alerte de budget.

## IA : gratuit d'abord, payant en secours

- `PRIMARY_PROVIDER=gemini` (par défaut) et `FALLBACK_PROVIDER=anthropic` par exemple.
- Un fournisseur **payant est ignoré** tant que `ALLOW_PAID_PROVIDERS=true`, la clé **et** les prix par million de tokens ne sont pas renseignés
  (sinon le budget ne peut pas être calculé). Le code ne contient aucun prix en dur.
- `GEMINI_FREE_TIER=true` seulement si vous avez vérifié votre quota gratuit et ses conditions (notamment l'usage des données
  envoyées) : à ce stade je ne l'ai pas vérifié. Idem pour le nom du modèle (`GEMINI_MODEL`).
- Plafonds : `BUDGET_DAILY`, `BUDGET_MONTHLY`, `MAX_REQUESTS_PER_DAY`, `MAX_SESSIONS_PER_DAY`. Le fournisseur de secours payant n'est
  appelé que si le principal échoue, et seulement dans les plafonds.
- Une session = 1 appel multimodal (toutes les pages) + 1 relecture texte-seule. Naviguer entre les réponses ne rappelle jamais l'IA.

## Réponses visuelles (schémas et tableaux)

Quand la consigne ou la réponse contient des termes comme « schéma », « MLD », « UML » ou « tableau »,
le backend crée localement une carte PNG avec Pillow et tente de l'envoyer dans Messenger en pièce jointe.
Ce rendu réutilise la réponse déjà produite : **aucun appel supplémentaire à Groq** n'est effectué.
La réponse textuelle habituelle reste envoyée, même si le téléversement de l'image échoue.

Il s'agit d'une première version : elle met en page la réponse structurée dans une image, mais ne transforme
pas encore automatiquement chaque MLD/UML en diagramme sémantique avec des boîtes et des flèches.
Le service Messenger doit autoriser le téléversement de pièces jointes et les limites de l'offre gratuite
Northflank restent applicables.

## Confidentialité

- Photos : supprimées dès l'analyse terminée (`DELETE_IMAGES_AFTER_ANALYSIS`), sinon après 2 h au plus.
- Résultats : supprimés après 48 h sauf `KEEP_HISTORY=true`. `supprime` efface tout immédiatement.
- Fichiers en droits 600/700 ; aucun contenu de document, aucune clé dans les journaux ; jeton Meta envoyé en en-tête, jamais dans l'URL.
- Webhook : signature `X-Hub-Signature-256` obligatoire (refus si le secret est absent), corps limité à 1 Mo, doublons ignorés.
- Téléchargement des photos : HTTPS uniquement, hôtes `fbcdn.net` / `fbsbx.com` / `facebook.com` / `cdninstagram.com`, sans redirection, taille limitée.
- **Vos photos transitent par Meta (Messenger) puis par le fournisseur d'IA choisi**, qui les traite selon ses propres conditions.

## Test à faire en premier (5 minutes, sans code)

Depuis les lunettes, sans toucher l'iPhone : prendre une photo, l'envoyer à un contact Messenger, en envoyer trois à la suite,
lire la réponse sur l'écran. Notez le nombre de gestes par photo et la lisibilité d'un message de 450 caractères
(réglage `SCREEN_CHARS`). Si l'envoi d'une photo exige la voix ou l'iPhone, il faudra revoir l'approche.

## Limites connues

- Pas d'écran de navigation sur mesure : la « navigation » est le défilement de la conversation (ou `qN`).
- Les lunettes ne permettent peut-être pas de choisir le destinataire/la photo sans autre geste : à mesurer.
- Le seuil de flou dépend de vos photos (feuilles très blanches = scores bas) : calibrez avec `scripts.analyze_files`.
- Une page illisible n'est détectée que par le flou à l'envoi, puis par le modèle (`lisible=false`).
- Messenger limite les réponses libres à une fenêtre après votre dernier message (suffisant ici : on répond à vos photos).
