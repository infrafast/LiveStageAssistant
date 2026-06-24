# LiveStageAssistant – Architecture RAG basée sur les MCP

## Objectif

Permettre à LiveStageAssistant de répondre à des questions spécialisées concernant :

- Consoles audio (XMSeries, X32, XR18, Wing, etc.)
- Mixing Station
- QLC+
- Protocoles OSC, MIDI, DMX, ArtNet, sACN
- Documentation des MCP
- Procédures techniques
- Guides utilisateurs
- Références API

sans intégrer directement cette connaissance dans le prompt système de l'agent.

---

# Principe fondamental

L'agent LiveStageAssistant doit rester totalement générique.

Il ne doit contenir aucune connaissance métier spécifique à :
- XMSeries
- QLC+
- Mixing Station
- Behringer
- Yamaha
- ou tout autre système métier

La connaissance doit être portée par les MCP eux-mêmes.

---

# Architecture cible

Chaque MCP expose :
- des tools
- des prompts
- des ressources de connaissance

L'agent :
- découvre les MCP
- récupère leurs prompts
- récupère leurs ressources knowledge
- les indexe dans son moteur RAG
- reste neutre vis-à-vis du domaine métier

---

# Ressource MCP standard

Ajouter une ressource standard :

knowledge

Exemples :

knowledge://xmseries/manual
knowledge://xmseries/api
knowledge://xmseries/osc

knowledge://qlcplus/userguide
knowledge://qlcplus/cues

knowledge://mixingstation/api
knowledge://mixingstation/manual

---

# Synchronisation

Au démarrage :

1. Découverte des MCP
2. Récupération des ressources knowledge
3. Mise en cache locale
4. Indexation dans le moteur RAG

Cache local recommandé :

knowledge/
├── xmseries/
├── qlcplus/
├── mixingstation/
└── ...

---

# Réutilisation du moteur RAG existant

IMPORTANT

Avant d'ajouter :
- ChromaDB
- FAISS
- LlamaIndex
- ou toute autre dépendance

vérifier si LiveStageAssistant dispose déjà d'un moteur RAG.

Exemples :
- LangChain Retrieval
- LangChain VectorStore
- LangGraph Knowledge
- autre moteur déjà intégré

Si un moteur RAG existe déjà, il doit être réutilisé en priorité.

Objectifs :
- éviter les dépendances supplémentaires
- réduire la taille de l'installation
- réduire la maintenance
- éviter plusieurs indexeurs concurrents

---

# Embeddings

Option préférée :

SentenceTransformers
all-MiniLM-L6-v2

Alternative :

OpenAI text-embedding-3-small

---

# Workflow d'une requête

Question utilisateur
↓
Recherche vectorielle
↓
Top 3 à 5 chunks pertinents
↓
Injection dans le prompt
↓
LLM
↓
Réponse

---

# Évolution future

Les ressources knowledge pourront contenir :

- documentation
- FAQ
- workflows
- exemples de commandes
- mappings
- procédures
- exemples de conversations
- profils de scène

---

# Principes de conception

1. L'agent reste neutre.
2. La connaissance appartient aux MCP.
3. Les MCP exposent des ressources knowledge.
4. Les ressources sont synchronisées localement.
5. Le RAG indexe ces ressources.
6. Le moteur RAG existant doit être réutilisé en priorité.
7. Aucune connaissance métier ne doit être codée dans l'agent.
8. L'ajout d'un MCP ajoute automatiquement sa connaissance.
9. Le système doit fonctionner sur Raspberry Pi 4.
10. Le LLM doit rester interchangeable (OpenAI, Ollama, etc.).
