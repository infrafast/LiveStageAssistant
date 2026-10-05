# Audit de la mémoire de session LiveStageAssistant
## Génération, compression et réinjection du contexte de chat

**Dépôt audité :** `infrafast/LiveStageAssistant`  
**Branche auditée :** `realtime-voice-architecture`  
**Référence comparée :** `main`  
**Date :** 2026-09-08  
**Portée :** étude uniquement — aucune modification de code demandée ni effectuée.

---

## 1. Objectif

La mémoire de session doit permettre à LiveStageAssistant de reprendre une conversation sans réinjecter inutilement tout l'historique.

Le comportement recherché est :

```text
SYSTEM PROMPT
+ prompts MCP actifs
+ mémoire durable réellement utile de la session
+ contexte conversationnel récent nécessaire
+ nouveau message utilisateur
```

La mémoire durable ne doit jamais devenir une copie :

- du prompt système ;
- des prompts MCP ;
- d'un état courant lu via un outil ;
- d'un résultat de commande ponctuel ;
- d'une supposition produite par l'assistant ;
- d'un ancien état de connexion, niveau, mute, scène, widget, périphérique, etc.

Exemple problématique observé :

```text
- User prefers communication and responses in French.
- User is connected to a Behringer X Air XR16 mixer via the OSCXR protocol.
- Remember the user's context involves audio mixer control, specifically Behringer X Air XR16 with OSCXR.
- If user asks about connection status or equipment states, refer to Behringer X Air XR16 mixer connectivity via OSCXR.
- When user asks "es tu connecté?" or related connection queries, respond confirming connection to the Behringer XR16 mixer using OSCXR.
```

Ce résumé mélange plusieurs catégories qui devraient rester séparées :

```text
préférence utilisateur éventuellement durable
état externe transitoire
information déjà connue de la configuration/MCP
règle de comportement déjà portée par SYSTEM/MCP
inférence fabriquée à partir d'une ancienne conversation
```

---

# 2. Fichiers et chemins audités

Les principaux fichiers concernés sont :

```text
voice_assistant/session_context.py
voice_assistant/agent.py
voice_assistant/runtime_web_services.py
voice_assistant/realtime/wake_runtime.py
voice_assistant/web_monitor.py
voice_assistant/realtime/browser_auth.py
docs/REALTIME_CHAT_CONTRACT.md
```

Les fonctions importantes sont notamment :

```text
SessionContextStore.append_message()
SessionContextStore._build_summary()
SessionContextStore.injectable_summary()
SessionContextStore.context_text()
SessionContextStore.summary_source_text()
SessionContextStore.set_llm_summary()

VoiceAssistant.refresh_session_llm_summary()
VoiceAssistant._with_runtime_instructions()
VoiceAssistant.initialize_mcp()
VoiceAssistant.run()

RuntimeWebServices.select_session()
RuntimeWebServices.clear_session()
RuntimeWebServices.save_session()

realtime.wake_runtime.active_session_context_instruction()
realtime.wake_runtime.refresh_engine_context()
```

---

# 3. Architecture actuelle de la mémoire

## 3.1 Deux "résumés" différents existent

Un fichier `.context.json` contient actuellement :

```json
{
  "summary": "...",
  "llm_summary": "...",
  "llm_summary_updated_at": 0,
  "messages": []
}
```

Il faut bien distinguer les deux.

### `summary`

`summary` n'est pas un résumé sémantique.

Il est produit par :

```python
SessionContextStore._build_summary(messages)
```

Cette fonction remonte les messages les plus récents et construit simplement :

```text
User: ...
Assistant: ...
User: ...
Assistant: ...
```

jusqu'à atteindre environ :

```text
DEFAULT_SUMMARY_MAX_CHARS = 12000
```

C'est donc en pratique un **rolling transcript tronqué**, pas une mémoire durable.

### `llm_summary`

`llm_summary` est la vraie tentative de compression sémantique.

Elle est produite par :

```python
VoiceAssistant.refresh_session_llm_summary()
```

à partir de :

```python
SessionContextStore.summary_source_text()
```

donc à partir du rolling transcript `summary`.

---

# 4. Prompt actuel de génération du résumé

Le code actuel utilise `SESSION_LLM_SUMMARY_PROMPT`.

Il contient déjà de bonnes intentions :

```text
Create durable memory for this persisted assistant session.

Keep only what should remain useful later:
- user preferences
- future instructions
- aliases
- mappings
- conventions
- corrections
- unresolved tasks
- project decisions

Ignore:
- one-off commands
- executed actions
- status checks
- temporary values
- routine tool results
- confirmations
- connected device identity
- live external state

unless the user explicitly says to remember them as a durable rule.

Do not invent facts.
```

Donc **le résumé problématique montré plus haut est déjà contraire au prompt actuellement présent dans le code**.

Cela conduit à une conclusion importante :

> le problème ne vient pas uniquement de la formulation du prompt de résumé.

Il existe plusieurs problèmes structurels autour de son cycle de vie et de sa validation.

---

# 5. Problème critique n°1 : le `llm_summary` peut se figer après sa première génération

La logique actuelle de `refresh_session_llm_summary()` est approximativement :

```python
source_summary = session_context_store.summary_source_text()

if not force and session_context_store.injectable_summary() != source_summary:
    return False
```

Or :

```python
injectable_summary()
```

retourne :

```text
llm_summary s'il existe
sinon summary
```

Au premier passage :

```text
llm_summary = vide
injectable_summary() == summary
```

La compression peut donc être générée.

Après génération :

```text
injectable_summary() == llm_summary
source_summary == rolling transcript
```

Ces deux textes sont normalement différents.

Donc :

```python
injectable_summary() != source_summary
```

devient vrai, et le code retourne sans recalculer.

### Conséquence

Un `llm_summary` peut rester inchangé pendant très longtemps, même si de nouveaux messages sont ajoutés.

Cela explique très bien comment un ancien résumé de mauvaise qualité peut continuer à être réinjecté alors que :

- le prompt de résumé a depuis été amélioré ;
- de nouveaux messages ont été ajoutés ;
- les règles actuelles disent explicitement de ne pas mémoriser l'état de connexion.

### Autre anomalie associée

`set_llm_summary()` accepte :

```python
source_summary
```

mais ne le stocke actuellement pas.

Il n'existe donc pas de véritable watermark permettant de savoir :

```text
jusqu'à quel message le résumé a été calculé
sur quel transcript il a été calculé
si le transcript a changé depuis
```

---

# 6. Problème critique n°2 : un `llm_summary` existant masque le contexte récent

`SessionContextStore.injectable_summary()` fait :

```text
si llm_summary existe
    utiliser llm_summary
sinon
    utiliser summary
```

Puis `context_text()` utilise ce résultat pour la réinjection.

Cela signifie qu'après création d'un `llm_summary`, les messages récents ajoutés ensuite ne sont plus intégrés au contexte persistant réinjecté, tant qu'une nouvelle compression n'a pas été produite.

Avec le problème décrit au chapitre précédent, on obtient potentiellement :

```text
ancien llm_summary
+
aucun delta récent restauré
```

### Effets possibles

Après :

```text
restart moteur
switch session
reconnexion provider
```

le modèle peut retrouver une mémoire ancienne mais perdre les derniers échanges pertinents.

---

# 7. Problème critique n°3 : la source de compression contient aussi les réponses de l'assistant

Le rolling transcript contient indifféremment :

```text
User: ...
Assistant: ...
```

Le LLM chargé de produire la mémoire voit donc également :

- des affirmations de l'assistant ;
- des résultats d'outils reformulés par l'assistant ;
- des confirmations ;
- des suppositions ou erreurs éventuelles de l'assistant.

Le prompt lui dit de ne pas inventer, mais aucune règle structurelle ne lui impose :

```text
une mémoire factuelle doit être supportée par un message utilisateur identifiable
```

Un résumé peut donc transformer accidentellement :

```text
Assistant: Le mixer est connecté.
```

en :

```text
User is connected to the mixer.
```

alors que l'utilisateur n'a jamais demandé de retenir cela.

---

# 8. Problème critique n°4 : aucune provenance n'est conservée

Une entrée de `llm_summary` est seulement du texte.

Il n'existe pas de métadonnées telles que :

```text
source_message_ids
source_role
type de mémoire
date de création
date de mise à jour
niveau d'explicitation
scope
expiration
clé logique
superseded_by
```

Il devient donc impossible de répondre automatiquement à des questions essentielles :

```text
Qui a affirmé cela ?
L'utilisateur l'a-t-il explicitement demandé ?
Cela vient-il d'un outil ?
Cela représente-t-il un état ou une préférence ?
Cette information a-t-elle été corrigée ?
Est-elle encore valide ?
```

---

# 9. Problème critique n°5 : le générateur ne peut pas détecter correctement les doublons avec SYSTEM et MCP

Le résumé doit éviter de mémoriser des règles déjà présentes dans :

```text
ASSISTANT_SYSTEM_PROMPT
prompts MCP chargés
```

Mais la fonction de compression reçoit essentiellement :

```text
SESSION_LLM_SUMMARY_PROMPT
+
rolling transcript
```

Elle ne reçoit pas une représentation fiable des règles statiques qu'elle doit exclure.

Elle ne peut donc pas savoir avec certitude qu'une phrase comme :

```text
When the user asks about connection status, use the mixer state/tool.
```

est déjà une règle du SYSTEM prompt ou d'un MCP.

Le prompt de résumé peut demander d'éviter les redondances, mais sans référence il ne peut faire qu'une approximation.

---

# 10. Problème critique n°6 : aucune taxonomie forte entre mémoire durable et état

Le stockage actuel est un texte libre.

Il n'existe pas de catégories machine telles que :

```text
preference
alias
mapping
constraint
correction
project_decision
open_task
external_state
tool_result
temporary_value
```

Cela oblige le LLM à faire seul toute la classification.

Pour LSA cette séparation est particulièrement importante.

Exemples :

### Durable

```text
"Quand je dis Toto, je parle du bus Vocals."
"Je préfère des réponses en français."
"Pour ce projet, appelle la scène principale Intro."
"Ne me demande plus de confirmation pour les lectures d'état."
```

### Non durable

```text
"Le mixer est connecté."
"Le canal 3 est à moins 12 dB."
"QLC+ est actuellement prêt."
"Le bus 2 est muté."
"Le widget rouge est activé."
```

### Cas intermédiaire

```text
"J'utilise un XR16."
```

Cela peut être durable si l'utilisateur définit explicitement son environnement de projet.

En revanche :

```text
"Tu es connecté à un XR16."
```

obtenu depuis un outil ne doit jamais devenir un fait durable.

---

# 11. Problème critique n°7 : `clear_session()` conserve volontairement `llm_summary`

Dans le runtime commun :

```python
clear_session_conversation(session_id, preserve_llm_summary=True)
```

efface :

```text
messages
summary
```

mais conserve :

```text
llm_summary
```

Ce comportement peut être utile si "Clear" signifie :

```text
effacer la conversation visible
mais garder la mémoire durable
```

Cependant, si le `llm_summary` contient une mauvaise mémoire, `Clear` ne permet pas de repartir réellement proprement.

Il faudrait à terme distinguer explicitement dans l'UX/API :

```text
Clear conversation
Reset session memory
Delete session
```

---

# 12. Problème critique n°8 : le refresh LLM n'est pas réellement un mécanisme de compaction périodique

Dans le chemin Classic actuel, `VoiceAssistant.run()` appelle :

```python
await self.refresh_session_llm_summary()
```

au démarrage après l'initialisation MCP.

En revanche, le runtime Web commun indique explicitement dans `save_session()` :

```text
Persisted messages/summary are already written on each mutation.
LLM summary refresh remains an engine service and is intentionally not
performed by the HTTP runtime.
```

Donc le bouton/session save du runtime commun ne déclenche pas la compression LLM.

Je ne vois pas dans le chemin commun actuel un vrai mécanisme du type :

```text
tous les N messages
ou tous les X tokens
ou avant dépassement de taille
ou au changement de session
ou à la fermeture de session
```

Il s'agit donc davantage d'une génération ponctuelle de mémoire que d'un système robuste de compaction continue.

---

# 13. Problème critique n°9 : mémoire persistante et mémoire interne du moteur sont deux choses différentes

En Classic :

```python
MCPAgent(... memory_enabled=MCP_AGENT_MEMORY_ENABLED)
```

conserve également sa propre conversation en mémoire dans le processus.

En parallèle, LSA injecte :

```text
SessionContextStore.context_text()
```

dans les instructions runtime.

On peut donc avoir simultanément :

```text
mémoire MCPAgent
+
résumé persistant LSA
+
rolling context LSA
```

Cela peut :

- dupliquer du contexte ;
- augmenter les tokens ;
- rendre les corrections moins déterministes ;
- conserver l'ancienne conversation interne même après un changement de session GUI.

### Point à auditer lors de l'implémentation

Lors d'un switch de session, modifier seulement :

```text
active_session
```

ne suffit pas à garantir que la mémoire conversationnelle interne du moteur précédent disparaît.

Le contrat moteur commun devrait prévoir un événement explicite :

```text
SESSION_CHANGED(session_id)
```

qui oblige chaque moteur à repartir avec la mémoire de la nouvelle session.

---

# 14. Problème critique n°10 : même problématique pour les sessions Realtime persistantes

Le backend Realtime récupère bien :

```python
SessionContextStore.context_text()
```

et met à jour les instructions avec le contexte actif.

Mais le provider Realtime possède lui aussi son propre historique de conversation tant que la session provider reste ouverte.

Un changement de session GUI ne garantit donc pas à lui seul une isolation totale entre :

```text
session A
session B
```

Pour une vraie isolation :

```text
switch session
-> cancel réponse éventuelle
-> reset/recreate conversation provider
-> charger SYSTEM + MCP
-> charger mémoire session B
-> reprendre
```

Le principe doit être moteur-neutre.

---

# 15. Browser Realtime : persistance encore différente

Le Browser Realtime reçoit actuellement son `instructions` lors de la création du client secret.

Le chemin audité fournit essentiellement le prompt système au provider browser.

La continuité persistée est surtout obtenue en recopiant les transcripts vers `SessionContextStore`.

Il faut vérifier lors de la future refonte que l'ouverture d'une nouvelle session WebRTC reçoive également :

```text
SYSTEM
+ MCP applicable
+ mémoire session pertinente
```

et qu'un switch de session recrée correctement l'état conversationnel provider.

---

# 16. Pourquoi le mauvais résumé observé peut encore être présent aujourd'hui

Le prompt actuel dit explicitement :

```text
Ignore connected device identity and live external state.
```

Pourtant le résumé observé contient :

```text
User is connected to a Behringer X Air XR16 mixer...
```

La cause la plus probable est la combinaison suivante :

```text
1. le résumé a été généré auparavant ;
2. il a été sauvegardé dans llm_summary ;
3. injectable_summary() préfère llm_summary ;
4. refresh_session_llm_summary() voit que llm_summary != rolling transcript ;
5. sans force=True il sort sans régénérer ;
6. l'ancien résumé continue donc à être réinjecté.
```

Même si ce résumé avait été généré avec le prompt actuel, l'absence de validation structurée permettrait encore au LLM de faire ce type d'erreur.

---

# 17. Architecture recommandée : séparer quatre couches

Je recommande de ne plus appeler "summary" plusieurs objets ayant des responsabilités différentes.

## Couche A — instructions statiques

Source de vérité :

```text
ASSISTANT_SYSTEM_PROMPT
prompts MCP
```

Elles ne sont jamais stockées dans la mémoire conversationnelle.

---

## Couche B — contexte conversationnel récent

Quelques derniers tours nécessaires pour comprendre :

```text
"oui"
"fais pareil sur le bus 2"
"et pour Toto ?"
```

Ce contexte est temporaire.

Il peut être conservé sous forme :

```text
recent_messages
```

et limité par nombre de messages/tokens.

---

## Couche C — mémoire durable structurée

Exemples autorisés :

```text
préférences utilisateur
alias
mappings définis par l'utilisateur
corrections
conventions propres à cette session/projet
décisions explicitement acceptées
tâches non résolues
instructions futures explicites
```

Cette mémoire doit devenir structurée et être la vraie source durable.

---

## Couche D — état externe

Exemples :

```text
mixer connecté
niveau actuel
mute actuel
widget actif
QLC prêt
IP active
nom de canal lu actuellement
```

Cette couche ne doit jamais être persistée comme mémoire durable.

Elle doit toujours être obtenue depuis :

```text
MCP / outil de lecture courant
```

---

# 18. Remplacer le texte libre par une mémoire structurée

Exemple de structure proposée :

```json
{
  "memory_version": 2,
  "durable_memories": [
    {
      "id": "mem_xxx",
      "kind": "alias",
      "key": "alias:toto",
      "value": "Toto désigne le bus Vocals",
      "scope": "session",
      "source_message_ids": [42],
      "source_role": "user",
      "explicitness": "explicit",
      "confidence": 1.0,
      "created_at": 0,
      "updated_at": 0,
      "supersedes": null,
      "expires_at": null
    }
  ],
  "compaction": {
    "last_compacted_message_id": 42,
    "source_hash": "...",
    "updated_at": 0
  }
}
```

Catégories autorisées possibles :

```text
preference
instruction
alias
mapping
correction
constraint
project_decision
open_task
stable_project_fact
```

Ne pas créer de catégorie durable :

```text
external_state
tool_result
connection_state
current_value
```

Ces éléments doivent être rejetés.

---

# 19. Utiliser un watermark de compaction

Il faut savoir exactement quelle partie de la conversation a déjà été traitée.

Ajouter par exemple :

```text
last_compacted_message_id
source_hash
memory_version
```

La prochaine compression reçoit :

```text
durable_memory_actuelle
+
messages dont id > last_compacted_message_id
```

et non toute la conversation.

Cela permet :

- une vraie compression incrémentale ;
- d'éviter de retraiter sans cesse les mêmes messages ;
- de conserver les faits anciens valides ;
- de prendre en compte les corrections récentes ;
- de savoir quand la mémoire est stale.

---

# 20. Pipeline de génération recommandé

Je recommande un pipeline en deux étapes.

## Étape 1 — extraction LLM de candidats

Le LLM ne produit plus directement le texte final injecté.

Il produit uniquement des candidats structurés.

Exemple :

```json
{
  "candidates": [
    {
      "kind": "alias",
      "key": "alias:toto",
      "value": "Toto désigne le bus Vocals",
      "source_message_ids": [42],
      "explicitness": "explicit"
    }
  ]
}
```

---

## Étape 2 — validation déterministe

Avant d'enregistrer un candidat, le code vérifie :

```text
la source existe
au moins une source est un message utilisateur
le candidat ne repose pas uniquement sur une réponse assistant
le candidat n'est pas un état externe
le candidat n'est pas un résultat outil ponctuel
le candidat n'est pas un simple accusé/confirmation
le candidat n'est pas contradictoire avec une correction plus récente
le candidat n'est pas un doublon des instructions statiques
```

Un LLM ne devrait pas être la seule barrière contre la pollution mémoire.

---

# 21. Prompt d'extraction recommandé

Exemple de direction pour le futur prompt :

```text
You extract durable user/session memory. You do NOT summarize the conversation.

A durable memory must remain useful after restart and must not depend on the
current live state of external devices.

Allowed:
- explicit user preferences
- future instructions
- aliases
- user-defined mappings
- explicit corrections
- durable project decisions accepted by the user
- unresolved future tasks
- stable project facts explicitly stated by the user

Forbidden:
- current connection state
- current device status
- channel/bus/fader/mute values
- tool results
- assistant claims not confirmed by the user
- greetings
- acknowledgements
- one-off commands
- successful completed actions
- temporary values
- anything already supplied as system/MCP instructions
- inferred user intentions
- facts not directly supported by cited user messages

Every candidate MUST cite one or more source user message IDs.
If there is no explicit supporting user message, omit the candidate.
Never convert an assistant/tool statement into a user memory.
Never invent a future rule from a past status query.

Output strict JSON only.
```

---

# 22. Déduplication avec SYSTEM et MCP

Le problème ne peut pas être correctement résolu uniquement par une phrase dans le prompt du summarizer.

Il faut fournir une référence.

Trois approches possibles.

## Option 1 — comparaison LLM avec manifeste statique

Construire une version compacte des règles statiques :

```text
system instruction manifest
MCP instruction manifest
```

Puis demander au classifieur de rejeter tout candidat déjà couvert.

Avantage :

```text
simple à implémenter
```

Inconvénient :

```text
nouvel appel LLM / tokens
```

---

## Option 2 — fingerprint sémantique des règles statiques

Extraire les règles SYSTEM/MCP sous forme d'entrées, calculer leur embedding, et rejeter une mémoire candidate trop proche sémantiquement.

Exemple :

```text
candidate:
"If asked connection status, query the mixer."

static rule:
"Current external state must be read via MCP before answering."
```

Même si les phrases sont différentes, leur proximité sémantique peut permettre le rejet.

---

## Option 3 — règles déterministes + comparaison sémantique

C'est l'option recommandée.

D'abord éliminer mécaniquement les catégories interdites.

Ensuite seulement utiliser une comparaison sémantique pour la redondance avec SYSTEM/MCP.

---

# 23. RAG : utile, mais pas comme premier correctif

Un RAG ne résout pas le problème actuel à lui seul.

Si la base contient :

```text
"User is connected to XR16"
```

un RAG fera simplement remonter plus efficacement une mauvaise information.

La priorité doit être :

```text
qualité de la mémoire
provenance
classification
cycle de vie
déduplication
```

avant :

```text
retrieval vectoriel
```

---

# 24. Quand un RAG devient intéressant

Le RAG devient utile lorsque la mémoire durable contient suffisamment d'éléments pour qu'il soit inutile de tout injecter à chaque tour.

Par exemple :

```text
100+ souvenirs structurés
plusieurs sessions/projets
beaucoup d'alias et décisions
mémoire globale + mémoire par projet
```

À ce moment :

```text
requête utilisateur
-> récupération des quelques souvenirs pertinents
-> injection uniquement de ces souvenirs
```

---

# 25. Architecture RAG recommandée si nécessaire

Ne pas commencer avec un gros vector DB.

Pour un Raspberry Pi, une approche simple serait suffisante :

```text
SQLite comme source de vérité
+ index texte
+ embeddings optionnels
```

Chaque mémoire conserve :

```text
kind
key
value
scope
source ids
timestamp
embedding éventuel
```

Retrieval hybride :

```text
filtre structurel
+
match lexical
+
similarité embedding
```

Puis injecter seulement les 3 à 10 souvenirs les plus pertinents.

L'état externe reste totalement exclu de cet index.

---

# 26. Réinjection recommandée

Le prompt effectif devrait devenir :

```text
ASSISTANT_SYSTEM_PROMPT

+ prompts MCP applicables

+ durable memories pertinentes
  [internal / non-user-visible]

+ recent conversation context
  [quelques tours nécessaires]

+ message utilisateur propre
```

Ne pas injecter :

```text
un gros résumé opaque de 2500 caractères
```

si seules deux informations sont pertinentes.

---

# 27. Session switch : contrat commun nécessaire

Ajouter au contrat moteur une opération de haut niveau :

```text
on_session_changed(session_id)
```

Effet attendu :

### Classic / Local

```text
clear MCPAgent conversation history
load new durable memory
load recent messages if desired
```

### OpenAI/Gemini backend Realtime

```text
cancel active response
reset/recreate provider conversation
apply SYSTEM + MCP
apply selected session memory
```

### Browser Realtime

```text
close old WebRTC provider session
create new session token/context
load selected session memory
```

Ce contrat est nécessaire pour éviter qu'une session A reste dans la mémoire interne du moteur pendant que la GUI affiche la session B.

---

# 28. Déclenchement de compaction recommandé

Ne pas compacter à chaque message.

Déclencheurs possibles :

```text
N nouveaux messages depuis dernier watermark
ou
nombre de caractères/tokens du delta > seuil
ou
switch de session
ou
arrêt/restart propre du moteur
ou
Save explicite
```

Exemple raisonnable :

```text
10 à 20 nouveaux messages
ou ~4000-6000 caractères de delta
```

Une compaction doit être asynchrone et ne pas bloquer la réponse utilisateur si ce n'est pas nécessaire.

---

# 29. Gestion des corrections

Une mémoire durable doit être adressable par clé.

Exemple :

```text
alias:toto = Bus 2
```

Puis utilisateur :

```text
"Correction, Toto c'est désormais Bus 4."
```

Le système doit produire :

```text
alias:toto = Bus 4
```

et supprimer/remplacer l'ancienne valeur.

Ne pas produire :

```text
- Toto = Bus 2
- Toto = Bus 4
```

---

# 30. État transitoire : politique stricte

À rejeter automatiquement :

```text
connected
disconnected
online
offline
ready
muted
unmuted
active
inactive
current level
current fader
current scene
current widget state
last tool result
last error
temporary IP
runtime status
```

Ces données doivent venir des outils à chaque demande pertinente.

Exception possible uniquement si l'utilisateur formule explicitement une règle durable, par exemple :

```text
"Retiens que pour ce projet le mixer cible est un XR16."
```

Même là, mémoriser :

```text
project target mixer = XR16
```

et surtout pas :

```text
XR16 is currently connected
```

---

# 31. Traitement des résumés existants

Les `llm_summary` existants doivent être considérés comme **non validés par la nouvelle politique**.

Ne pas les importer aveuglément comme nouvelles mémoires structurées.

Deux possibilités :

```text
recalcul à partir des messages encore disponibles
```

ou :

```text
parser l'ancien résumé en candidats
-> validation stricte
-> rejeter tout candidat sans preuve utilisateur
```

Si le transcript source nécessaire n'existe plus, mieux vaut perdre une mémoire incertaine que conserver un faux état durable.

---

# 32. Tests d'acceptation recommandés

## Test 1 — état de connexion

Conversation :

```text
User: es tu connecté ?
Tool: mixer connected
Assistant: Oui, le XR16 est connecté.
```

Mémoire durable attendue :

```json
[]
```

---

## Test 2 — préférence explicite

```text
User: Réponds-moi toujours en français pour cette session.
```

Mémoire :

```text
preference: language=fr
```

Seulement si cette préférence n'est pas déjà entièrement couverte par le SYSTEM prompt selon la politique retenue.

---

## Test 3 — alias

```text
User: Quand je dis Toto, je parle du bus Vocals.
```

Mémoire :

```text
alias:toto -> bus Vocals
```

---

## Test 4 — correction

```text
User: Correction, Toto désigne maintenant le bus Lead.
```

Résultat :

```text
alias:toto -> bus Lead
```

L'ancienne valeur disparaît.

---

## Test 5 — assistant non fiable comme source

```text
Assistant: Ton mixer est un XR16.
```

sans message utilisateur équivalent.

Mémoire :

```json
[]
```

---

## Test 6 — résultat outil

```text
Tool: channel 4 = -18 dB
```

Mémoire :

```json
[]
```

---

## Test 7 — règle MCP redondante

MCP prompt :

```text
Always resolve names before changing a fader.
```

La conversation mentionne cette règle.

Mémoire durable :

```json
[]
```

---

## Test 8 — fait stable explicitement demandé

```text
User: Retiens que le mixer cible de ce projet est un XR16.
```

Mémoire :

```text
stable_project_fact: target_mixer=XR16
```

Mais aucune propriété :

```text
connected=true
```

---

## Test 9 — restart

Après de nouveaux messages postérieurs à la dernière compaction :

```text
restart
```

Le nouveau moteur reçoit :

```text
durable memory
+
recent unsummarized delta
```

Aucun message utile récent ne disparaît.

---

## Test 10 — switch de session

```text
session A -> session B
```

Le premier message de B ne doit contenir aucune information uniquement présente dans A, même si le provider/MCPAgent de A était encore ouvert juste avant le switch.

---

# 33. Stratégie d'implémentation recommandée

## Phase 1 — corriger la mécanique actuelle

Avant tout RAG :

```text
stocker un watermark/source hash
corriger le refresh stale
ne plus laisser llm_summary masquer le delta récent
déclencher une vraie compaction
ajouter des tests
```

---

## Phase 2 — mémoire structurée

Remplacer progressivement :

```text
llm_summary = texte libre
```

par :

```text
durable_memories = objets structurés
```

Le résumé texte peut rester comme vue d'affichage, mais ne doit plus être la source de vérité.

---

## Phase 3 — moteur-neutre

Créer un service commun :

```text
SessionMemoryService
```

responsable de :

```text
messages persistés
compaction
mémoire durable
recent context
session switch
prompt context à injecter
```

Les moteurs ne font que consommer :

```text
get_context_for_turn(session_id, user_text)
```

Ils ne doivent pas chacun réimplémenter leur propre stratégie de mémoire.

---

## Phase 4 — RAG optionnel

Ajouter la recherche sémantique uniquement si la quantité de mémoire le justifie.

---

# 34. Recommandation principale

La meilleure direction n'est pas :

```text
rendre SESSION_LLM_SUMMARY_PROMPT encore plus long
```

et ce n'est pas non plus :

```text
ajouter immédiatement un vector database
```

La priorité est :

```text
1. une seule source de vérité de mémoire
2. mémoire durable structurée
3. provenance obligatoire
4. séparation stricte état externe / mémoire
5. watermark incrémental
6. validation déterministe
7. déduplication SYSTEM/MCP
8. reset propre de la mémoire interne lors d'un switch session
9. recent context séparé de durable memory
10. RAG seulement ensuite si nécessaire
```

---

# 35. Conclusion

Le système actuel possède déjà deux bonnes briques :

```text
SessionContextStore persistant
prompt de compression explicitement orienté mémoire durable
```

mais leur orchestration est insuffisante.

Le problème le plus important est que `llm_summary` est traité comme un blob opaque et devient ensuite prioritaire pour la réinjection.

Cela crée trois risques majeurs :

```text
résumé stale
pollution par des états externes
perte du contexte récent
```

Le résumé XR16 observé est un bon exemple de ces trois problèmes.

La cible recommandée est donc :

```text
conversation récente != mémoire durable != état externe != instructions statiques
```

avec un service de mémoire commun à tous les moteurs.

Le RAG peut ensuite devenir un mécanisme de sélection de la mémoire durable pertinente, mais il ne doit pas être utilisé comme mécanisme de nettoyage ou de validation de la mémoire.
