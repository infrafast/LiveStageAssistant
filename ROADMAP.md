# LiveStageAssistant — Roadmap de simplification Wake Word / Audio

## Objectif

Refondre et simplifier le pipeline audio de LiveStageAssistant autour des principes suivants :

- **openWakeWord est l’unique moteur de wake word.**
- **Silero V6 reste le moteur VAD de référence pour l’endpointing des commandes.**
- Le chemin historique `generic` / `post_stt` doit disparaître.
- Le wake word reste **optionnel** :
  - `WAKE_WORD=` → aucun wake word.
  - `WAKE_WORD=<valeur>` → openWakeWord obligatoire.
- Le microphone backend doit être géré par une **machine d’état audio explicite**.
- La feature d’interruption vocale doit être intégrée à cette même machine d’état, sans créer de second pipeline audio concurrent.
- STT et reconnaissance du locuteur doivent être parallélisés lorsque possible.
- La Web GUI, les profils `.env`, les tests et les fichiers Markdown doivent être mis à jour dans le même changement.
- Ne pas conserver de fallback silencieux vers un ancien moteur de wake word.

Le dépôt connaît déjà son contexte fonctionnel. Respecter les règles de maintenance présentes dans `AGENT.md`.

## Suivi d'implémentation

- [x] Jalon 1 — retirer `BACKEND_WAKE_WORD_MODE` du runtime, de la sauvegarde `.env`, des profils, de la Web GUI et de la documentation utilisateur. Le backend ne retombe plus silencieusement sur le gate wake word post-STT quand `WAKE_WORD` est défini mais openWakeWord est indisponible.
- [ ] Jalon 2 — introduire la machine d'état audio explicite `WAIT_WAKE` / `CAPTURE_COMMAND` / `PROCESSING` / `TTS`.
- [ ] Jalon 3 — intégrer l'interruption vocale à cette machine d'état sans second listener concurrent.
- [ ] Jalon 4 — paralléliser STT et speaker recognition sur les segments audio acceptés.
- [ ] Jalon 5 — ajouter les timings/debug openWakeWord et les tests de non-régression.

---

# 1. Architecture cible

## 1.1 Wake word désactivé

Lorsque :

```env
WAKE_WORD=
```

le pipeline doit être :

```text
micro backend
    ↓
Silero V6
CAPTURE_COMMAND
    ↓
audio terminé
    ↓
┌─────────────────────┐
│ STT                 │
│ Speaker recognition │
│ en parallèle        │
└──────────┬──────────┘
           ↓
          LLM
           ↓
          TTS
           ↓
CAPTURE_COMMAND
```

Dans ce mode :

- openWakeWord ne doit pas être exécuté ;
- aucune logique de wake word textuelle ne doit être appliquée ;
- aucun seuil/cooldown/pre-roll wake word ne doit intervenir.

## 1.2 Wake word activé

Lorsque :

```env
WAKE_WORD=momo
```

le pipeline doit être :

```text
micro backend permanent
         ↓
┌─────────────────┐
│    WAIT_WAKE    │
│  openWakeWord   │
└────────┬────────┘
         │ wake détecté
         ▼
┌─────────────────┐
│ CAPTURE_COMMAND │
│    Silero V6    │
└────────┬────────┘
         │
         ▼
┌─────────────────────┐
│ STT                 │
│ Speaker recognition │
│ en parallèle        │
└──────────┬──────────┘
           ↓
       PROCESSING
           ↓
          TTS
           ↓
       WAIT_WAKE
```

Responsabilités :

```text
openWakeWord
→ détecte uniquement le wake word

Silero V6
→ détecte le début et la fin de la commande

STT
→ transcrit uniquement

Speaker recognition
→ identifie le locuteur

LLM / MCP
→ interprète et exécute la commande
```

---

# 2. Supprimer complètement le chemin generic / post-STT

Supprimer l’ancienne architecture wake word :

```text
generic
post_stt
fallback wake word via Whisper
BACKEND_WAKE_WORD_MODE
```

Supprimer également les alias et branches associées si elles existent encore dans le code.

La règle doit devenir :

```text
WAKE_WORD vide
→ wake word désactivé

WAKE_WORD défini
→ openWakeWord obligatoire
```

Si `WAKE_WORD` est défini mais que openWakeWord ou le modèle ONNX ne peut pas être chargé :

- produire une erreur explicite ;
- afficher clairement que le wake word est indisponible ;
- ne jamais basculer silencieusement vers un wake gate STT.

---

# 3. Introduire une vraie machine d’état audio

Centraliser le pipeline backend autour d’états explicites :

```text
WAIT_WAKE
CAPTURE_COMMAND
PROCESSING
TTS
```

Avec wake word :

```text
WAIT_WAKE
→ CAPTURE_COMMAND
→ PROCESSING
→ TTS
→ WAIT_WAKE
```

Sans wake word :

```text
CAPTURE_COMMAND
→ PROCESSING
→ TTS
→ CAPTURE_COMMAND
```

Réduire autant que possible les flags actuels du type :

```text
streaming_wake_active
wake_detected
wake_command_armed
streaming_wake_detected
wake_command_wait_ms
```

Les transitions d’état doivent être lisibles, centralisées et testables.

---

# 4. Un seul propriétaire du microphone backend

Le backend audio doit éviter les ouvertures/fermetures répétées du flux micro.

En `WAIT_WAKE`, le microphone doit rester ouvert et alimenter openWakeWord continuellement.

Une conversation ambiante ou une détection de parole ne doit pas provoquer :

```text
Silero détecte parole
→ capture
→ rejet
→ fermeture micro
→ réouverture micro
```

Silero V6 ne doit pas endpoint la parole ambiante en `WAIT_WAKE`.

En `WAIT_WAKE` :

```text
micro → openWakeWord
```

En `CAPTURE_COMMAND` :

```text
micro → Silero V6
```

Le flux physique doit idéalement rester le même entre les deux états.

---

# 5. Repenser le son d’écoute

Le son `LISTENING_SOUND_FILE` ne doit plus être joué avant l’ouverture effective du microphone.

Éviter :

```text
play listening sound
→ ouvrir micro
→ écouter wake
```

En mode wake word :

- ouvrir / maintenir le micro d’abord ;
- entrer en `WAIT_WAKE` immédiatement ;
- ne pas imposer de son avant la détection.

Si un feedback sonore est souhaité, il peut être joué après le wake word comme accusé de réception, à condition de ne pas rendre obligatoire une pause entre `momo` et la commande.

Le système doit continuer à supporter :

```text
momo monte Claude
```

prononcé sans pause.

---

# 6. Simplifier la transition wake → commande

Ne plus essayer de retirer parfaitement le wake word au niveau audio.

Le trigger openWakeWord peut arriver assez tard pour que le frame courant contienne déjà le début de la commande.

Préférer :

- conserver un petit overlap audio sûr autour du trigger ;
- démarrer Silero V6 pour la commande ;
- laisser Whisper transcrire normalement.

Whisper peut retourner :

```text
momo monte Claude
```

Puis effectuer uniquement un nettoyage textuel :

```text
momo monte Claude
→ monte Claude
```

Ce nettoyage ne doit jamais décider si le wake word était valide. La décision d’autorisation appartient exclusivement à openWakeWord.

---

# 7. Supprimer le second wake gate après STT

En mode wake word :

```text
openWakeWord a déclenché
→ commande autorisée
```

Ne plus exécuter un second contrôle du type :

```python
apply_wake_word(...)
should_process
```

pour autoriser/refuser la commande backend.

Supprimer les bypass du type :

```text
if not should_process and streaming_wake_detected
```

Créer si nécessaire une fonction légère dédiée uniquement au nettoyage :

```text
strip_leading_wake_word_if_present()
```

Cette fonction ne valide rien et ne rejette rien.

Lorsque `WAKE_WORD=` aucun traitement wake-word textuel ne doit être appliqué.

---

# 8. Séparer les buffers wake et commande

Ne pas réutiliser un gros `BACKEND_WAKE_WORD_PRE_ROLL_MS` comme buffer de commande.

Séparer conceptuellement :

```text
wake detector context
```

et :

```text
command pre-roll / speech pad
```

La commande Silero doit avoir un petit pre-roll dédié, à benchmarker autour de :

```text
100–250 ms
```

Le but est :

- ne pas perdre le premier phonème de la commande ;
- ne pas envoyer 1,6 s de bruit/silence/wake inutile à Whisper.

Réévaluer l’utilité de `BACKEND_WAKE_WORD_PRE_ROLL_MS` et le supprimer si son rôle devient inutile.

---

# 9. Conserver Silero V6 comme VAD de référence

Silero V6 reste le moteur d’endpointing de la commande.

Ne pas remplacer Silero V6 par le VAD interne openWakeWord.

openWakeWord peut éventuellement conserver son VAD interne uniquement comme filtre de wake-word si utile.

Silero V6 doit gérer :

- début de parole ;
- durée minimale de parole ;
- fin de parole ;
- durée minimale de silence ;
- speech pad / command pre-roll ;
- durée maximale de commande.

---

# 10. Optimiser les paramètres Silero V6

Le profil actuel utilise notamment :

```env
VAD_MIN_SILENCE_MS=900
```

Benchmark obligatoire sur de vraies commandes avec :

```text
700 ms
600 ms
500 ms
400 ms
```

Cible probable à tester en priorité :

```text
500–600 ms
```

Tester également `VAD_MIN_SPEECH_MS` pour les commandes courtes.

Cas de validation :

```text
monte Claude
coupe façade
mute Mike
mets Claude à moins douze
mets Claude... à moins douze
```

---

# 11. Renforcer la décision openWakeWord

Le wrapper actuel ne doit pas se limiter à :

```text
un frame >= threshold
→ wake
```

Évaluer :

- le mécanisme `patience` d’openWakeWord ;
- ou une confirmation temporelle équivalente clairement implémentée et testée.

Exemple conceptuel :

```text
2 frames valides sur 3
```

Prévoir que plusieurs modèles puissent avoir des calibrations différentes :

```text
momo.onnx
console.onnx
regie.onnx
```

Ne pas supposer qu’un seuil unique sera toujours optimal.

---

# 12. VAD interne openWakeWord : uniquement comme filtre wake éventuel

Conserver l’option de tester :

```env
BACKEND_WAKE_WORD_VAD_THRESHOLD=
BACKEND_WAKE_WORD_VAD_THRESHOLD=0.3
BACKEND_WAKE_WORD_VAD_THRESHOLD=0.4
BACKEND_WAKE_WORD_VAD_THRESHOLD=0.5
```

Ce VAD interne openWakeWord doit uniquement servir à limiter les faux positifs provoqués par des bruits non vocaux.

Il ne remplace pas Silero V6 pour `CAPTURE_COMMAND`.

---

# 13. Refaire proprement la feature d’interruption

La feature d’interruption doit être intégrée à la machine d’état principale.

Éviter l’architecture actuelle avec plusieurs listeners concurrents.

Le pipeline backend doit avoir :

```text
UN flux micro
UNE machine d’état
UN openWakeWord
UN Silero V6
```

## Interruption désactivée

Lorsque :

```env
INTERRUPT_CONVERSATION_ENABLED=false
```

pendant :

```text
PROCESSING
TTS
```

aucune nouvelle commande vocale n’est acceptée.

## Interruption activée + wake word actif

Lorsque :

```env
INTERRUPT_CONVERSATION_ENABLED=true
WAKE_WORD=momo
```

openWakeWord doit pouvoir rester actif pendant `PROCESSING` et `TTS`.

Si `momo` est détecté pendant `PROCESSING` :

```text
wake
→ annuler process_task
→ arrêter thinking sound
→ CAPTURE_COMMAND
→ Silero V6 capture la nouvelle commande
```

Si `momo` est détecté pendant `TTS` :

```text
wake
→ stop TTS
→ CAPTURE_COMMAND
→ Silero V6 capture la nouvelle commande
```

Toute interruption vocale doit donc commencer par le wake word lorsqu’un wake word est configuré.

## Interruption activée sans wake word

Lorsque :

```env
WAKE_WORD=
INTERRUPT_CONVERSATION_ENABLED=true
```

Silero V6 peut détecter une nouvelle parole pendant `PROCESSING` / `TTS` si ce comportement est conservé pour compatibilité.

Ce mode est naturellement plus sensible au bruit ambiant et doit être explicitement testé.

---

# 14. Revoir la protection post-TTS

Supprimer les resets openWakeWord répétés à chaque chunk pendant une période de suppression.

Préférer :

```text
fin TTS
→ changement d’état
→ reset unique si nécessaire
→ warm-up contrôlé
→ WAIT_WAKE
```

Si l’interruption est activée pendant TTS, traiter explicitement le risque que le micro entende le TTS.

Ne pas masquer ce problème avec une accumulation de sleeps/resets arbitraires.

---

# 15. Paralléliser STT et reconnaissance de locuteur

Le pipeline actuel ne doit plus faire :

```text
Whisper
→ Resemblyzer
→ LLM
```

Utiliser :

```text
            ┌→ Whisper ──────────┐
audio ──────┤                    ├→ LLM
            └→ Resemblyzer ──────┘
```

Lancer les deux traitements simultanément lorsque la reconnaissance speaker est activée.

Si la reconnaissance speaker est désactivée :

```text
audio → STT → LLM
```

sans délai supplémentaire.

---

# 16. Instrumenter les performances

Ajouter des timings structurés en mode debug.

Mesurer au minimum :

```text
wake_detected
command_speech_start
command_speech_end
stt_start
stt_end
speaker_start
speaker_end
llm_start
llm_response
tts_start
tts_end
```

Pouvoir produire un résumé du type :

```text
Wake detection: 160 ms
Command endpointing silence: 520 ms
STT: 810 ms
Speaker: 390 ms
Pre-LLM total: 1.34 s
LLM: 620 ms
Total to response: 1.96 s
```

---

# 17. Ajouter un vrai debug openWakeWord

En mode debug uniquement, pouvoir inspecter :

```text
model
score courant
score max récent
threshold
VAD score éventuel
trigger/reject
```

Exemple :

```text
openWakeWord momo: max=0.48 threshold=0.60 rejected
```

ou :

```text
openWakeWord momo: score=0.72 threshold=0.60 triggered
```

Ne pas spammer les logs normaux.

---

# 18. Construire un corpus réel de validation

Prévoir un corpus WAV enregistré depuis le vrai matériel / vrai micro.

Inclure :

```text
wake seul
wake + commande immédiate
wake + petite pause
wake prononcé doucement
wake prononcé fortement
plusieurs locuteurs
distance proche
distance éloignée
musique
paroles ambiantes
mots proches phonétiquement
bruit de scène
longues périodes sans wake
```

Réutiliser exactement les mêmes WAV après chaque modification.

Mesurer :

```text
wake recall
false negatives
false positives
false positives/hour
latence de détection
latence fin de commande
```

Ne pas retravailler `momo.onnx` avant d’avoir stabilisé le runtime.

---

# 19. Évaluer le modèle wake seulement après la refonte

Après stabilisation du pipeline :

- tester au moins plusieurs dizaines de vrais wakes ;
- tester plusieurs heures d’audio négatif représentatif ;
- observer la distribution réelle des scores.

Seulement ensuite décider de :

```text
modifier threshold
modifier patience
réentraîner le modèle
ajouter des negative samples
changer de wake word
```

---

# 20. Simplifier la configuration `.env`

Supprimer :

```env
BACKEND_WAKE_WORD_MODE
```

et toutes les anciennes variables uniquement liées au chemin generic/post-STT.

Configuration cible avec wake :

```env
WAKE_WORD=momo
BACKEND_WAKE_WORD_MODEL_PATHS=data/wake_words/momo.onnx
BACKEND_WAKE_WORD_THRESHOLD=0.60
BACKEND_WAKE_WORD_VAD_THRESHOLD=
BACKEND_WAKE_WORD_COOLDOWN_MS=1200
```

Réévaluer la nécessité de :

```env
BACKEND_WAKE_WORD_PRE_ROLL_MS
```

selon la nouvelle implémentation.

Sans wake :

```env
WAKE_WORD=
```

Dans ce cas openWakeWord ne doit pas exécuter de modèle wake.

Mettre à jour tous les profils réellement utilisés :

```text
.env.example
raspi_service_pack_stdio/.env.online
raspi_service_pack_stdio/.env.offline
autres profils actifs présents dans le repo
```

Respecter les règles de `AGENT.md`.

---

# 21. Modifier la Web GUI

La GUI doit refléter la nouvelle architecture.

## Supprimer

Tout contrôle permettant de sélectionner :

```text
generic
post_stt
openwakeword
BACKEND_WAKE_WORD_MODE
```

Il n’y a plus de moteur à choisir.

## Nouveau comportement Wake Word

Dans la section STT / Wake Word :

```text
Wake Word
Model
Wake Threshold
Wake VAD Threshold
Cooldown
```

Règle UI :

```text
Wake Word vide
→ wake word désactivé

Wake Word renseigné
→ openWakeWord activé automatiquement
```

Le modèle doit rester sélectionnable parmi les `.onnx` compatibles présents sous `data/`.

## Statut openWakeWord

Ajouter ou conserver un statut clair, par exemple :

```text
Wake Word: momo
Engine: openWakeWord
Model: momo.onnx
Status: Ready
```

En cas d’erreur :

```text
Status: Model unavailable
```

ou :

```text
Status: openWakeWord initialization failed
```

Ne jamais afficher un moteur fallback inexistant.

## Interruption

Conserver le réglage :

```env
INTERRUPT_CONVERSATION_ENABLED
```

Modifier son texte d’aide pour expliquer le nouveau comportement.

Sens attendu : lorsqu’un wake word est actif, le prononcer pendant que l’assistant réfléchit ou parle interrompt l’action en cours et démarre une nouvelle commande vocale.

Lorsque aucun wake word n’est configuré, adapter le texte pour expliquer que toute nouvelle parole détectée peut interrompre l’assistant si ce comportement est conservé.

## Traductions

Toute modification de texte GUI doit être répercutée dans :

```text
assets/i18n/*
```

Ne pas modifier uniquement le français ou l’anglais.

---

# 22. Documentation Markdown

Faire un audit global des fichiers `.md` pour supprimer les anciennes descriptions.

Rechercher notamment :

```text
generic
post_stt
BACKEND_WAKE_WORD_MODE
wake-word gate after STT
fallback wake word
```

## `README.md`

Garder une documentation utilisateur courte.

Expliquer simplement :

```text
WAKE_WORD vide
→ wake word désactivé

WAKE_WORD défini
→ openWakeWord utilisé
```

Documenter brièvement le modèle ONNX nécessaire.

Ne pas placer le détail de la machine d’état dans le README.

## `docs/ARCHITECTURE.md`

Documenter complètement la nouvelle architecture :

```text
WAIT_WAKE
CAPTURE_COMMAND
PROCESSING
TTS
```

Inclure :

- microphone backend permanent ;
- openWakeWord ;
- Silero V6 ;
- wake → command transition ;
- interruption pendant PROCESSING ;
- interruption pendant TTS ;
- post-TTS rearm ;
- STT + speaker recognition en parallèle ;
- comportement avec `WAKE_WORD=`.

Ajouter un diagramme clair.

## `raspi_service_pack_stdio/README.md`

Mettre à jour :

- configuration Raspberry ;
- modèle ONNX ;
- variables wake word ;
- diagnostic de chargement ;
- absence de fallback generic ;
- comportement en cas d’erreur modèle.

## Autres fichiers Markdown

Vérifier tous les fichiers Markdown concernés par :

- wake word ;
- audio backend ;
- VAD ;
- installation openWakeWord ;
- Raspberry Pi ;
- interface Web.

Ne laisser aucune documentation décrivant `generic` ou `post_stt` comme moteur encore supporté.

---

# 23. Tests automatisés

Les tests doivent couvrir la machine d’état réelle, pas uniquement le wrapper `Model.predict()`.

Ajouter des tests pour :

```text
WAKE_WORD vide
WAKE_WORD défini
openWakeWord init success
openWakeWord init failure
absence de fallback
WAIT_WAKE continu
conversation ambiante sans wake
wake détecté
wake + commande dans le même chunk
wake + commande sans pause
wake + commande avec pause
spike unique sous politique patience
Silero V6 speech start
Silero V6 speech end
command timeout
post-TTS rearm
interrupt pendant PROCESSING
interrupt pendant TTS
interrupt désactivée
speaker recognition disabled
speaker recognition enabled
STT + speaker parallélisés
```

Ajouter des tests spécifiques garantissant que le flux micro / state machine ne revient pas au vieux comportement de capture/rejet permanent en `WAIT_WAKE`.

---

# 24. Nettoyage final

Après validation du nouveau pipeline, supprimer définitivement :

```text
generic
post_stt
legacy wake gate
BACKEND_WAKE_WORD_MODE
fallback text wake
dead flags
duplicate interrupt listeners
obsolete helpers
obsolete env variables
obsolete tests
obsolete documentation
```

Faire une recherche globale avant de considérer le travail terminé.

---

# 25. Vérifications finales

Respecter `AGENT.md`.

Au minimum :

```bash
.venv/bin/python -m py_compile voice_assistant/agent.py voice_assistant/web_monitor.py
```

```bash
pytest
```

```bash
git diff --check
```

Vérifier également :

- `.env.example`
- profils Raspberry
- Web GUI
- tous les `assets/i18n/*`
- `README.md`
- `docs/ARCHITECTURE.md`
- `raspi_service_pack_stdio/README.md`
- autres `.md` contenant les anciens termes.

---

# Contraintes de non-régression

Ne pas casser :

- commandes web texte ;
- injection de commandes via Web GUI ;
- microphone navigateur ;
- diagnostic audio backend ;
- reconnaissance locuteur ;
- TTS backend ;
- TTS navigateur ;
- changement de profil / reload env ;
- MCP tool routing ;
- context/session handling ;
- monitoring Web ;
- mode `WAKE_WORD=` sans wake word ;
- interruption désactivée ;
- interruption activée.

Les chemins audio Web qui n’utilisent pas le microphone backend peuvent garder leur logique propre si nécessaire, mais ils ne doivent pas réintroduire un moteur backend `generic/post_stt`.

---

# Critères d’acceptation

Le travail est considéré terminé lorsque :

1. `BACKEND_WAKE_WORD_MODE` n’existe plus comme choix runtime ou GUI.
2. Aucun fallback backend generic/post-STT n’existe.
3. `WAKE_WORD=` désactive réellement openWakeWord.
4. `WAKE_WORD=<valeur>` active obligatoirement openWakeWord.
5. Le microphone backend reste stable en `WAIT_WAKE`.
6. Silero V6 n’endpoint pas la parole ambiante avant le wake.
7. Le wake word n’est pas revalidé par Whisper.
8. Le début des commandes sans pause après le wake n’est pas coupé.
9. STT et speaker recognition peuvent fonctionner en parallèle.
10. L’interruption utilise la même machine d’état et non un second pipeline audio concurrent.
11. La GUI ne présente plus de mode generic/post-STT.
12. Les profils `.env` sont nettoyés.
13. Les traductions GUI sont cohérentes.
14. La documentation Markdown décrit uniquement la nouvelle architecture.
15. Les tests de state machine, wake, VAD et interruption sont verts.
16. Les timings permettent d’identifier clairement les latences.
17. Aucun comportement fallback silencieux ne masque une panne openWakeWord.

---

# Architecture finale visée

```text
                         WAKE_WORD défini

                         ┌─────────────────┐
micro backend permanent ─►    WAIT_WAKE    │
                         │  openWakeWord   │
                         └────────┬────────┘
                                  │ wake
                                  ▼
                         ┌─────────────────┐
                         │ CAPTURE_COMMAND │
                         │    Silero V6    │
                         └────────┬────────┘
                                  │
                                  ▼
                       ┌─────────────────────┐
                       │ STT        Speaker  │
                       │    en parallèle     │
                       └──────────┬──────────┘
                                  │
                                  ▼
                             PROCESSING
                                  │
                                  ▼
                                 TTS
                                  │
                                  ▼
                              WAIT_WAKE
```

Avec :

```env
INTERRUPT_CONVERSATION_ENABLED=true
```

openWakeWord peut provoquer :

```text
PROCESSING → CAPTURE_COMMAND
TTS        → CAPTURE_COMMAND
```

après détection du wake word.

Sans wake word :

```text
                         WAKE_WORD vide

micro backend
     ↓
CAPTURE_COMMAND
Silero V6
     ↓
STT + Speaker
en parallèle
     ↓
PROCESSING
     ↓
TTS
     ↓
CAPTURE_COMMAND
```

C’est l’architecture cible à implémenter.
