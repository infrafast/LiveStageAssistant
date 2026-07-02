# Recette vocale Live Stage Assistant

Date : ____________________  
Opérateur : ____________________  
Version LiveStageAssistant : ____________________  
Version XMSeries-MCP : ____________________

## Consignes avant de commencer

- Effectuer cette recette hors spectacle, sans public et à un niveau sonore sûr.
- Prévenir les personnes présentes avant les tests de mute, fade et façade.
- Ajouter le mot de réveil configuré devant chaque commande si nécessaire, par exemple `Console, ...` ou `Régie, ...`.
- Attendre la réponse complète de l’assistant avant de prononcer la commande suivante.
- Consulter simultanément le journal du service dans un autre terminal :

```bash
journalctl -u livestageassistant.service -f
```

- Noter les valeurs initiales afin de pouvoir restaurer la console :

| Élément | Valeur initiale |
|---|---:|
| Façade / Main LR | |
| Bus Claude | |
| Channel `guitar-clode` | |
| Envoi `guitar-clode` vers Claude | |

## 1. État général et identité

- [X] « Es-tu prêt à recevoir des commandes ? »
- [X] « À quel mixeur es-tu connecté ? »
- [X] « Quel est le modèle du mixeur ? »
- [X] « Quel protocole de mixage utilises-tu ? »
- [ ] « Quel est l’état de la connexion avec le mixeur ? »
- [ ] « Fais-moi un résumé de la console. »
- [X] « Qui suis-je ? »
- [X] « Détecte ma voix. »
- [X] « Quel est mon retour ? »
- [X] « Quel est mon micro ? »

Notes :

____________________________________________________________________

## 2. Façade / Main LR

### Lecture et changements immédiats

- [X] « Quel est le niveau de la façade ? »
- [X] « Monte la façade. »
- [X] « Baisse la façade de 3 dB. »
- [X] « Mets la façade à moins 5 dB. »
- [X] « Mets la façade à 50 pour cent. »

### Mute et unmute

- [X] « Coupe explicitement la façade. »
- [X] « Rallume explicitement la façade. »

### Automations

- [X] « Fais un fade-out de la façade en 5 secondes. »
- [X] « Fais un fade-in de la façade jusqu’à 0 dB en 5 secondes. »
- [X] « Dans 5 secondes, mets la façade à moins 3 dB. »
- [/] « Baisse la façade de 3 dB, attends 5 secondes, puis remonte-la de 3 dB. »

Notes :

____________________________________________________________________

## 3. Fader maître du bus Claude

Ces commandes concernent le bus Claude lui-même, pas l’envoi d’un channel vers Claude.

- [X] « Quel est le niveau du bus Claude ? »
- [X] « Monte un peu le retour Claude. »
- [X] « Baisse le bus Claude de 3 dB. »
- [X] « Mets le bus Claude à moins 6 dB. »
- [ ] « Coupe le retour Claude. »
- [ ] « Rallume le retour Claude. »
- [ ] « Fais descendre progressivement le bus Claude à moins 20 dB en 8 secondes. »
- [ ] « Fais remonter progressivement le bus Claude à moins 6 dB en 8 secondes. »

Notes :

____________________________________________________________________

## 4. Channel `guitar-clode` vers la façade

Ces commandes concernent le fader principal du channel, donc son niveau vers la façade.

### Résolution exacte et structurée

- [ ] « Quel est le niveau de la tranche guitar-clode ? »
- [ ] « Monte la guitare de Claude. »
- [ ] « Baisse guitar-clode de 3 dB. »
- [ ] « Mets guitar-clode sur moins 8 dB. »
- [ ] « Monte guitar-clode à moins 6 dB. »

### Mute et unmute

- [ ] « Coupe guitar-clode. »
- [ ] « Réactive la guitare de Claude. »

### Automations

- [ ] « Fais un fade-out de guitar-clode en 5 secondes. »
- [ ] « Fais un fade-in de la guitare de Claude jusqu’à moins 6 dB en 5 secondes. »

Notes :

____________________________________________________________________

## 5. Envoi de `guitar-clode` vers le bus Claude

Ces commandes doivent modifier uniquement l’envoi du channel vers le retour Claude. Elles ne doivent modifier ni le fader principal de `guitar-clode`, ni le fader maître du bus Claude.

### Lecture et changements immédiats

- [ ] « Quel est le niveau de guitar-clode sur le retour Claude ? »
- [ ] « Monte la guitare de Claude sur Claude. »
- [ ] « Baisse de 3 dB guitar-clode sur le bus Claude. »
- [ ] « Mets guitar-clode sur Claude à moins 5 dB. »
- [ ] « Monte le volume guitar-clode sur Claude. »

### Mute et unmute de l’envoi

- [ ] « Coupe guitar-clode sur le bus Claude. »
- [ ] « Rallume guitar-clode sur le bus Claude. »

> En mode OSCXR, un mute spécifique channel-vers-bus peut être déclaré non supporté. L’assistant doit alors refuser proprement sans muter globalement le channel.

### Automations de l’envoi

- [ ] « Fais un fade-out de guitar-clode sur Claude en 6 secondes. »
- [ ] « Fais un fade-in de la guitare de Claude sur Claude jusqu’à moins 5 dB en 6 secondes. »
- [ ] « Dans 5 secondes, mets guitar-clode sur Claude à moins 10 dB. »
- [ ] « Baisse guitar-clode sur Claude de 4 dB, attends 5 secondes, puis remonte le même envoi de 4 dB. »

Notes :

____________________________________________________________________

## 6. Résolution fuzzy et sécurité

Pour chaque commande fuzzy, vérifier que l’assistant demande une confirmation et qu’aucune valeur ne change avant cette confirmation.

### Bus fuzzy

- [ ] « Monte le bus Clode. »
- [ ] Répondre : « Non, annule. »
- [ ] Vérifier que le bus Claude n’a pas changé.

### Channel fuzzy

- [ ] « Monte gitar-clode. »
- [ ] Répondre : « Oui, je confirme la tranche guitar-clode. »
- [ ] Vérifier que seul le channel confirmé a changé.

### Destination fuzzy

- [ ] « Monte guitar-clode sur Clode. »
- [ ] Répondre : « Non, annule. »
- [ ] Vérifier qu’aucun envoi n’a changé.

### Cible inexistante

- [ ] « Monte le saxophone de Luc. »
- [ ] Vérifier que l’assistant demande une précision et n’effectue aucune action.

Notes :

____________________________________________________________________

## 7. Contexte de conversation

Exécuter les commandes de chaque groupe dans l’ordre indiqué.

### Absence d’héritage implicite

- [ ] « Mets guitar-clode sur Claude à moins 8 dB. »
- [ ] « Monte le volume. »
- [ ] Vérifier que la seconde commande concerne la façade et non l’envoi précédent.

### Référence explicite au même envoi

- [ ] « Mets guitar-clode sur Claude à moins 8 dB. »
- [ ] « Monte encore le même envoi de 1 dB. »
- [ ] Vérifier que seul l’envoi `guitar-clode` vers Claude est modifié.

### Référence explicite au même bus

- [ ] « Mets le bus Claude à moins 8 dB. »
- [ ] « Monte le même bus de 2 dB. »
- [ ] Vérifier que le bus Claude arrive à moins 6 dB.

Notes :

____________________________________________________________________

## 8. Gestion des automations

Lancer d’abord une automation suffisamment longue pour pouvoir l’observer et l’annuler.

- [ ] « Fais descendre progressivement le bus Claude à moins 30 dB en 30 secondes. »
- [ ] « Liste les automatisations en cours. »
- [ ] Noter l’identifiant retourné : ____________________
- [ ] « Annule l’automatisation numéro [identifiant]. »
- [ ] « Liste les automatisations en cours. »
- [ ] « Annule l’automatisation numéro 999999. »
- [ ] Vérifier que le dernier ordre échoue proprement sans autre modification.

Notes :

____________________________________________________________________

## 9. Variantes STT importantes

- [ ] « Monte le son. »
- [ ] Vérifier que le STT n’écrit pas « montre le son ».
- [ ] « Monte la basse sur Claude. »
- [ ] Vérifier que `basse` est compris comme une source et non comme l’ordre `baisse`.
- [ ] « Baisse la basse sur Claude. »
- [ ] Vérifier que le premier mot est la direction et le second la source.
- [ ] Prononcer une commande avec le mot de réveil « Console ».
- [ ] Vérifier que le STT n’écrit pas seulement « sole ».
- [ ] Prononcer une commande avec le mot de réveil « Régie ».
- [ ] Vérifier que la commande franchit correctement le filtre du mot de réveil.

Notes :

____________________________________________________________________

## 10. Restauration de la console

Remplacer chaque valeur entre crochets par la valeur initiale notée au début.

- [ ] « Mets la façade à [niveau initial façade] dB. »
- [ ] « Mets le bus Claude à [niveau initial bus Claude] dB. »
- [ ] « Mets guitar-clode à [niveau initial channel] dB. »
- [ ] « Mets guitar-clode sur Claude à [niveau initial envoi] dB. »
- [ ] Vérifier que la façade, le bus, le channel et l’envoi ne sont pas mutés.
- [ ] « Liste les automatisations en cours. »
- [ ] Annuler toute automation encore active.

## 11. Bilan

| Domaine | Réussi | Échec | Notes |
|---|:---:|:---:|---|
| État général et mixeur | ☐ | ☐ | |
| Reconnaissance du locuteur | ☐ | ☐ | |
| Façade | ☐ | ☐ | |
| Bus Claude | ☐ | ☐ | |
| Channel `guitar-clode` | ☐ | ☐ | |
| Envoi `guitar-clode` vers Claude | ☐ | ☐ | |
| Résolution structurée | ☐ | ☐ | |
| Résolution fuzzy et confirmation | ☐ | ☐ | |
| Automations | ☐ | ☐ | |
| STT et mot de réveil | ☐ | ☐ | |
| Restauration finale | ☐ | ☐ | |

Anomalies principales :

____________________________________________________________________

____________________________________________________________________

