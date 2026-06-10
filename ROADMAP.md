Voici le résumé contextuel pour reprendre dans une autre session.

Objectif Général
On a transformé le web monitor en vraie interface web de pilotage type ChatGPT pour Live Stage Assistant, tout en gardant le mode embarqué/backend existant. Le backend Python reste le cerveau MCP/LLM; le navigateur peut maintenant servir de client texte, micro, TTS, stop/cancel, et mode conversation.

Fichiers Principalement Modifiés

voice_assistant/web_monitor.py
voice_assistant/agent.py
README.md
.env.example
container/config/.env.infrafast a aussi des changements locaux visibles, à vérifier prudemment car c’est une config active.
Interface Web GPT-like
La page principale affiche uniquement:

bulles utilisateur alignées droite;
bulles assistant/TTS alignées gauche;
champ texte en bas;
bouton envoyer flèche vers le haut;
bouton stop carré pendant traitement;
icône settings en haut à droite.
Les infos techniques ont été déplacées dans l’overlay:

tab Monitor: State, Console Log, Prompt;
tab Config: Config, LLM provider/model, ElevenLabs voice, OpenAI web TTS voice/speed, thinking sound.
Dialogue vs Logs
Ajout d’un historique de dialogue séparé:

WebMonitor.append_dialogue(role, text)
snapshot expose messages
les logs techniques restent dans logs.
Le monitor capture maintenant:

stdout
stderr
logging.StreamHandler déjà créés avant la capture
Les logs OSC ne sont plus filtrés côté web monitor. Les [OSC READ], /xinfo, [OSC WRITE], etc. doivent apparaître dans Settings > Monitor > Console Log.

Busy / Thinking / Stop
Ajout de l’état:

assistant_busy
Pendant traitement:

input désactivé;
bulle thinking avec trois points;
bouton envoyer devient bouton stop carré;
endpoint /api/cancel-command;
la task agent active est annulée et l’assistant revient à l’écoute.
Après fin de dialogue:

focus automatiquement rendu au champ texte, sauf si settings overlay est ouvert.
Thinking Sound Web
Pendant que la bulle à trois points est visible:

le navigateur joue en boucle le THINKING_SOUND_FILE sélectionné;
les fichiers sont servis via /assets/<filename>;
snapshot expose thinking_sound_url.
Voice Cancel During Thinking
Ajout expérimental:

VOICE_CANCEL_DURING_THINKING=false
Quand false:

aucun listener micro supplémentaire;
comportement normal inchangé.
Quand true:

pendant thinking, écoute courte parallèle;
annule si phrase claire: stop, stoppe, annule, annuler, arrête, arrete, cancel, etc.
Documenté comme expérimental et désactivé par défaut.

Web Audio
Ajout d’un mode web audio optionnel:

CONNECTIVITY_MODE=online
WEB_AUDIO_ENABLED=false
WEB_STT_PROVIDER=openai
WEB_STT_MODEL=whisper-1
WEB_RECORDING_MAX_SECONDS=8
WEB_CONVERSATION_SILENCE_MS=1200
WEB_CONVERSATION_IDLE_SECONDS=25
WEB_CONVERSATION_THRESHOLD=0.05
WEB_CONVERSATION_MIN_SPEECH_MS=350
WEB_CONVERSATION_MIN_SPEECH_FRAMES=8
WEB_TTS_PROVIDER=openai
WEB_TTS_MODEL=gpt-4o-mini-tts
WEB_TTS_VOICE=alloy
WEB_TTS_SPEED=1.00
Principe:

le navigateur capture le micro;
envoie l’audio au backend;
backend appelle OpenAI STT;
le texte transcrit est injecté comme une commande normale;
backend garde les clés API, aucune clé dans le navigateur.
Endpoints ajoutés:

POST /api/web-transcribe
POST /api/web-tts
Priorité TTS
Règle choisie:

le TTS backend a priorité;
si TTS_PROVIDER != none, le web TTS est désactivé pour éviter double audio;
pour utiliser TTS web, mettre TTS_PROVIDER=none;
si TTS Output = Browser est sauvé depuis la config web, WEB_AUDIO_ENABLED est aussi forcé à true pour garder le fichier .env cohérent.
Connectivité
Ajout d’un commutateur CONNECTIVITY_MODE dans la config web:

online expose les options cloud LLM/STT/TTS;
offline masque les options cloud STT/TTS, affiche TTS: local pyttsx3, et sauvegarde un profil local cohérent avec LLM_PROVIDER=ollama, STT_PROVIDER=local-whisper, CLOUD_TTS_PROVIDER=none, TTS_PROVIDER=pyttsx3, WEB_AUDIO_ENABLED=false et WEB_TTS_PROVIDER=none.
Push-to-talk Web
Bouton micro:

clic démarre l’enregistrement;
bouton devient carré et reste cliquable;
reclic arrête;
arrêt automatique après silence de fin de phrase;
timeout dur via WEB_RECORDING_MAX_SECONDS.
La détection de silence utilise:

WEB_CONVERSATION_THRESHOLD
WEB_CONVERSATION_SILENCE_MS
WEB_CONVERSATION_MIN_SPEECH_MS
WEB_CONVERSATION_MIN_SPEECH_FRAMES
Mode Conversation Web
Ajout bouton ∞ à côté du micro.
Quand activé:

push-to-talk grisé;
écoute continue côté navigateur;
détection parole/silence localement;
chaque segment est envoyé au backend STT;
si WAKE_WORD est configuré, le backend applique la wake word;
sans WAKE_WORD, chaque phrase détectée est traitée;
pause pendant thinking et TTS web;
reprise après réponse/TTS.
OpenAI Web TTS Config
Dans la page Config:

dropdown OpenAI Voice;
slider OpenAI Speed.
Voix fixes ajoutées:

echo masculin
onyx masculin
nova féminin
shimmer féminin
La vitesse:

slider 0.60x à 1.80x;
sauvegardée dans WEB_TTS_SPEED;
appliquée via audio.playbackRate.
Note: les voix OpenAI ne sont pas récupérées par API comme ElevenLabs. Elles sont une liste fixe documentée.

HTTPS / Micro Navigateur
Pour getUserMedia():

localhost/127.0.0.1 fonctionne généralement en HTTP;
depuis un autre appareil sur LAN (http://NAS_IP:8765), Chrome peut exiger HTTPS;
recommandé: reverse proxy HTTPS devant le monitor.
Tests Effectués
On a vérifié régulièrement:

.venv/bin/python -m py_compile voice_assistant/web_monitor.py voice_assistant/agent.py
git diff --check
tests HTTP locaux pour:
/api/snapshot
/api/cancel-command
/api/web-transcribe
/api/web-tts
/api/llm-config
/assets/thinking.wav
tests de logique wake word backend pour le mode conversation
tests de présence HTML pour éléments UI clés
À Vérifier En Reprise

Nettoyer prudemment container/config/.env.infrafast:
un diff a montré WEB_TTS_VOICE=alloy dupliqué;
WEB_TTS_SPEED=1.00 ajouté.
Ne pas écraser sans vérifier le fichier réellement actif.
Tester en navigateur réel:
push-to-talk arrêt silence;
mode conversation ∞;
wake word en mode conversation;
thinking sound web;
OpenAI voice/speed.
Vérifier si le reverse proxy HTTPS est nécessaire sur Synology/LAN.
Eventuellement ajouter une option UI pour activer/désactiver web audio depuis Config, mais actuellement c’est piloté par .env.
Commandes utiles

.venv/bin/python -m py_compile voice_assistant/web_monitor.py voice_assistant/agent.py
git diff --check
git status --short
État Conceptuel
Le système a maintenant 3 modes complémentaires:

mode embarqué backend audio: STT_PROVIDER / TTS_PROVIDER;
mode web texte/chat: toujours disponible si web monitor actif;
mode web audio: optionnel via WEB_AUDIO_ENABLED, avec STT/TTS OpenAI proxifiés par backend.

Comportement MCP dégradé
Si l'initialisation MCP échoue au démarrage, l'assistant ne quitte plus immédiatement. Le web monitor reste disponible, l'état MCP passe en erreur dans Settings > Monitor > State, et l'utilisateur peut corriger le profil env/MCP depuis Config puis sauvegarder pour déclencher un reload.

MCP Servers dans Config
Le web monitor expose maintenant un collapsible Settings > Config > MCP Servers avec un commutateur HTTP proxy / Direct. En mode proxy, Open et le chargement manuel de frame passent par /api/mcp-admin/<server>/..., ce qui permet d'utiliser une page runtime/admin même si seul le backend LiveStageAssistant/NAS voit le serveur MCP via Tailscale. En mode direct, le navigateur ouvre l'URL MCP lui-même. Les serveurs stdio restent visibles comme non embarquables; les headers Bearer configurés sont appliqués côté backend et ne sont pas exposés au navigateur.
