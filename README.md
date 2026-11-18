# M3U Watch (version serveur avec interface graphique)

Surveille tes serveurs IPTV (lien M3U ou lien Xtream) et envoie sur Telegram, selon le
calendrier de ton choix, les chaînes, films et séries ajoutés, avec leurs jaquettes.

* **Interface graphique** : ajouter / modifier des serveurs, tester le serveur et Telegram,
  régler la planification, voir pour chaque serveur le dernier envoi, les totaux et les ajouts
  (au format `chaînes - films - séries`).
* **Service d'arrière-plan** : fait les vérifications et les envois, même quand la fenêtre est
  fermée ou que la session RDP est déconnectée.
* **Fichier `config.json`** : tout se règle aussi à la main (voir `config.example.json`).
  L'interface le relit automatiquement quand tu le modifies.

## Installation (Lubuntu / Ubuntu / Debian)

1. Copie le dossier sur le serveur (par exemple dans `~/m3u-watch`).
2. Dans un terminal : `cd ~/m3u-watch && bash install.sh`
   (installe Python et Tk, crée le service `m3u-watch` et le lanceur « M3U Watch »).
3. Ouvre **M3U Watch** depuis le menu des applications ou l'icône du bureau.

Sans installation : `sudo apt install python3 python3-tk`, puis `python3 m3u_gui.py` pour
l'interface et `python3 m3u_monitor.py --daemon` pour le service.

## Utilisation

1. **Ajouter un serveur** : nom, lien du serveur, token du bot Telegram, chaîne.
   Utilise **Tester le serveur** (affiche le nombre de chaînes, films et séries, et l'état du
   compte) et **Tester Telegram** (envoie un message de test).
2. **Paramètres** : date et heure du premier envoi, récurrence (tous les jours, 2 ou 3 jours,
   toutes les semaines, ou personnalisée), nombre d'éléments détaillés, envoi des jaquettes.
3. Le premier passage d'un serveur **enregistre simplement le catalogue** et envoie un message
   « surveillance initialisée ». Les envois suivants ne contiennent que les changements.
4. **Vérifier maintenant** lance une vérification immédiate (sur le serveur sélectionné, ou sur
   tous) et envoie sur Telegram.

### Le tableau

| Colonne | Contenu |
|---|---|
| Dernier envoi | Date du dernier message envoyé sur Telegram pour ce serveur |
| Total | Nombre de chaînes - films - séries du catalogue, à la dernière vérification |
| Ajoutés | Nombre de chaînes - films - séries ajoutés lors du dernier envoi |
| Dernière vérif. | Date de la dernière lecture du serveur (même sans changement) |
| Statut | OK, Initialisé, À vérifier, Désactivé, Erreur de lecture, Erreur Telegram |

Sélectionne une ligne pour voir le détail d'une erreur sous le tableau. Un message n'est envoyé
que s'il y a du nouveau. Les nouveaux épisodes de séries sont signalés dans le message et dans
le détail sous le tableau.

## Telegram

1. Crée un bot avec **@BotFather** (`/newbot`) et copie le token.
2. Ajoute le bot comme **administrateur** de ta chaîne, avec le droit de publier.
3. Chaîne : `@nom_public`, ou `-100…` si elle est privée.

## Fichiers et commandes

* `config.json` : réglages et serveurs (contient tes tokens : lisible par ton utilisateur seul).
* `state/` : historique, statistiques et journal (`state/m3u_watch.log`).
* `systemctl status m3u-watch` · `journalctl -u m3u-watch -f` : état et journal du service.
* `python3 m3u_monitor.py --status` : résumé en ligne de commande.
* `python3 m3u_monitor.py --run-now` : vérification immédiate de tous les serveurs.
* Variable `M3U_WATCH_HOME` : change le dossier de `config.json` et `state/`.

## Remarques

* Si un envoi Telegram échoue, l'état du serveur n'est pas enregistré : les changements seront
  renvoyés au prochain passage au lieu d'être perdus.
* Si le fichier M3U de ton fournisseur est vide mais que son API Xtream répond, le catalogue est
  lu par l'API automatiquement.
* Si tu modifies le lien d'un serveur, son historique repart de zéro (pas de fausse alerte).
