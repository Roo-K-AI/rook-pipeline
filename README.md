# ROOK Pipeline - FastAPI Service

Service FastAPI interne dédié à l'enrichissement de produits et à l'orchestration asynchrone des jobs de génération pour le projet ROOK.

Ce service implémente le contrat d'API inter-services (`product-enrichment-request.v1` / `product-enrichment-ack.v1` / `product-enrichment-status.v1`) et intègre un **registre mémoire temporaire** (`InMemoryJobRegistry`) pour suivre le cycle de vie complet des jobs sans dépendre de PostgreSQL ni de Gemini dans cette phase.
Ce service implémente le contrat d'API inter-services (`product-enrichment-request.v1` / `product-enrichment-ack.v1` / `product-enrichment-status.v1`), un registre mémoire temporaire (`InMemoryJobRegistry`), et un moteur de pipeline modulaire asynchrone (`JobOrchestrator`).

---

## Arborescence du projet

```text
FastAPI/
├── README.md                      # Documentation du service, arborescence et guide d'exécution
├── requirements.txt               # Dépendances Python (FastAPI, Uvicorn, Pydantic)
├── app/                           # Code source de l'application FastAPI
│   ├── __init__.py                # Package Python app
│   ├── main.py                    # Point d'entrée de l'API, configuration et routes (/health, /ready)
│   ├── routers/                   # Routeurs d'API découpés par domaine
│   ├── core/                      # Configuration centrale et sécurité
│   │   ├── __init__.py            # Package core
│   │   ├── config.py              # Paramètres applicatifs (Settings, tokens, timeouts, version)
│   │   └── security.py            # Vérification du credential de service (Authorization)
│   ├── pipeline/                  # Moteur et étapes modulaires du pipeline d'enrichissement
│   │   ├── __init__.py            # Package pipeline
│   │   ├── collector.py           # Étape 1 : Collecte de sources avec protection anti-SSRF
│   │   ├── extractor.py           # Étape 2 : Extraction de faits et contrôle des allégations
│   │   ├── normalizer.py          # Étape 3 : Normalisation des données et génération de slugs
│   │   ├── context_builder.py     # Étape 4 : Construction du contexte structuré
│   │   ├── validator.py           # Étape 5 : Validation technique et décision requires_human_review
│   │   └── orchestrator.py        # Moteur asynchrone d'exécution (BackgroundTasks)
│   ├── routers/                   # Routeurs d'API
│   │   ├── __init__.py            # Package routers
│   │   └── enrichments.py         # Endpoints POST et GET /internal/v1/product-enrichments
│   │   └── enrichments.py         # Endpoints POST, GET et POST /jobs/{job_id}/retry
│   ├── schemas/                   # Schémas de validation Pydantic et modèles de données
│   │   ├── __init__.py            # Package schemas
│   │   └── enrichment.py          # Modèles de requête, d'accusé de réception (Ack) et de statut
│   └── services/                  # Logique métier et registres internes
│       ├── __init__.py            # Package services
│       └── job_registry.py        # Registre mémoire temporaire, gestion d'idempotence et cycle de vie
└── tests/                         # Tests automatisés
    ├── __init__.py                # Package tests
    ├── test_job_registry.py       # Tests unitaires du registre et des routes (idempotence, 404, 409, cycle)
    ├── test_pipeline_orchestrator.py # Tests des composants du pipeline et du endpoint retry
    └── test_e2e_http.py           # Tests d'intégration HTTP de bout en bout avec Uvicorn
```

---

## Description détaillée des composants

### 1. Point d'entrée (`app/main.py`)
- Initialise l'application FastAPI avec le titre `"ROOK Product Enrichment API"`.
- Expose les sondes d'observabilité :
  - `GET /health` : vérification de base (`status: "ok"`).
  - `GET /ready` : état de préparation du service (`status: "ready"`).
- Monte le routeur des enrichissements de produits sous le préfixe `/internal/v1/product-enrichments`.
### 1. Configuration & Sécurité (`app/core/`)
- **`config.py`** : centralise les variables de configuration (`SERVICE_AUTH_TOKEN`, `PIPELINE_VERSION`, `STEP_DELAY_SECONDS`, `DEFAULT_MAX_ATTEMPTS`).
- **`security.py`** : vérification stricte de l'en-tête `Authorization: Bearer <token>` provenant de Laravel.

### 2. Routeur (`app/routers/enrichments.py`)
Gère les interactions REST internes de Laravel vers FastAPI :
### 2. Moteur du Pipeline (`app/pipeline/`)
Architecture modulaire exécutée en tâche de fond via `FastAPI.BackgroundTasks` :
1. **`collector.py`** : validation stricte anti-SSRF (rejet des IPs privées, loopback et métadonnées cloud `169.254.169.254`) et collecte des sources officielles/techniques.
2. **`extractor.py`** : extraction des faits vérifiés sans invention de caractéristiques non prouvées (`verification_required`).
3. **`normalizer.py`** : nettoyage des espaces, normalisation des noms, marques, catégories et génération de slug.
4. **`context_builder.py`** : mise en forme du contexte pour le modèle d'enrichissement.
5. **`validator.py`** : contrôle de schéma et détection des alertes entraînant le statut `needs_review` ou `completed`.
6. **`orchestrator.py`** : orchestre l'enchaînement asynchrone des étapes et met à jour en temps réel la progression (`progress_percent`) et l'étape (`current_step`).

### 3. Routeur (`app/routers/enrichments.py`)
- **`POST /internal/v1/product-enrichments`** :
  - Valide les en-têtes obligatoires : `Authorization`, `X-Correlation-Id`, `Idempotency-Key`.
  - Crée un nouveau job en mémoire ou retourne le job existant associé à la clé d'idempotence.
  - Retourne un accusé de réception immédiat (`202 Accepted`) avec le statut initial `accepted` et l'étape `queued`.
  - Renvoie une erreur `409 Conflict` si la même clé d'idempotence est réutilisée avec un corps de requête différent.
  - Crée le job en mémoire et lance le pipeline en tâche de fond.
  - Retourne `202 Accepted` immédiatement.
  - Détecte les conflits d'idempotence (`409 Conflict`).
- **`GET /internal/v1/product-enrichments/jobs/{job_id}`** :
  - Récupère l'état courant du job et ses métadonnées.
  - Retourne `404 Not Found` si le `job_id` est inexistant.
  - Retourne l'état en temps réel et le résultat final (`ProductEnrichmentResult`).
  - Retourne `404 Not Found` si le `job_id` est introuvable.
- **`POST /internal/v1/product-enrichments/jobs/{job_id}/retry`** :
  - Permet de relancer un job échoué (`failed` ou `timed_out`) dans la limite des tentatives autorisées (`max_attempts`).

### 3. Schémas de données (`app/schemas/enrichment.py`)
Modèles Pydantic v2 garantissant le respect strict des contrats contractuels :
- `JobStatus` : énumération des statuts (`accepted`, `queued`, `collecting`, `extracting`, `normalizing`, `building_context`, `generating`, `validating`, `completed`, `needs_review`, `failed`, `timed_out`, `cancelled`).
- `ProductEnrichmentRequest` : validation de la demande entrante (nom de produit entre 3 et 255 caractères, version de schéma, locale, indices de marque et catégorie).
- `ProductEnrichmentAck` : structure de réponse `202 Accepted`.
- `ProductEnrichmentStatus` : structure de suivi d'état avec résultat éventuel.

### 4. Registre mémoire des jobs (`app/services/job_registry.py`)
Composant central de stockage temporaire en mémoire :
- **`JobRecord`** : structure stockant l'état interne complet du job (identifiants, progression, horodatages UTC, empreinte SHA-256 du payload, résultat et erreurs).
- **`InMemoryJobRegistry`** :
  - Protégé contre les accès concurrents via un verrou (`threading.Lock`).
  - Gère l'idempotence stricte et la détection de conflit (409).
  - Valide les transitions autorisées du cycle de vie :
    $$\text{accepted} \to \text{queued} \to \text{collecting} \to \text{extracting} \to \dots \to \text{validating} \to \text{completed / needs\_review / failed}$$
  - Empêche les transitions illégales depuis des états terminaux.
- **`get_job_registry()`** : fonction de dépendance FastAPI permettant l'injection automatique via `Depends()`.
- **`JobRecord`** : modèle en mémoire complet (états, progression, tentatives, empreinte SHA-256 du payload, résultat et erreurs).
- **`InMemoryJobRegistry`** : thread-safe (`threading.Lock`), gère l'idempotence stricte, les transitions d'état et le mécanisme de reprise (`reset_for_retry`).

### 5. Tests (`tests/`)
- **`tests/test_job_registry.py`** : teste unitairement le registre mémoire, les transitions d'états du cycle de vie, la déduplication d'idempotence, ainsi que les codes HTTP (`202`, `401`, `404`, `409`, `422`).
- **`tests/test_e2e_http.py`** : lance un serveur Uvicorn réel en arrière-plan et valide l'ensemble du flux HTTP via le module standard `urllib`.

---

## Guide d'exécution

### 1. Activer l'environnement virtuel
```bash
cd /home/mialy/Profession-amzay/ROOK/rook-pipeline/FastAPI
source .venv/bin/activate
```

### 2. Exécuter les tests automatisés
### 2. Exécuter la suite de tests
```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

### 3. Démarrer le serveur en local
```bash
uvicorn app.main:app --reload --port 8000
```

### 4. Endpoints principaux
- **Documentation interactive Swagger** : [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Documentation ReDoc** : [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
- **Documentation Swagger** : [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Healthcheck** : `GET http://127.0.0.1:8000/health`
- **Demande d'enrichissement** : `POST http://127.0.0.1:8000/internal/v1/product-enrichments`
- **Consultation de job** : `GET http://127.0.0.1:8000/internal/v1/product-enrichments/jobs/{job_id}`

- **Création de job** : `POST http://127.0.0.1:8000/internal/v1/product-enrichments`
- **Statut de job** : `GET http://127.0.0.1:8000/internal/v1/product-enrichments/jobs/{job_id}`
- **Retry de job** : `POST http://127.0.0.1:8000/internal/v1/product-enrichments/jobs/{job_id}/retry`
