> Guide détaillé de Maze en français : fonctionnalités, déploiement et exploitation. Présentation du projet en anglais : [README.md](../../README.md).

# Maze

Application web auto-hébergée pour préparer et suivre des campagnes de jeu de rôle.

Premier module livré : **PNJ et factions**, sous forme de carte interactive (graphe) où chaque point est un PNJ ou une faction, relié aux autres par des relations typées.

## Fonctionnalités du module PNJ et factions

- Carte interactive : déplacement libre des fiches (positions enregistrées), zoom, cadrage, export PNG.
- Réorganisation automatique lisible : plusieurs dispositions sont calculées, les fiches sont écartées pour que chaque lien soit assez long pour son étiquette et qu'aucune étiquette ne chevauche une autre étiquette, une fiche ou un lien ; la plus lisible (moins de croisements, de liens confondus) est retenue.
- PNJ : portrait, fonction, statut (vivant, mort, disparu, sort inconnu), accroche, apparence, personnalité, motivations, secrets MJ, notes, étiquettes, galerie d'images.
- Factions : couleur (reprise sur l'anneau de ses membres), emblème, type, description, objectifs, ressources, secrets, notes, liste des membres.
- Appartenances : un PNJ peut appartenir à plusieurs factions, avec un rôle et un indicateur "dirige".
- Relations entre PNJ et/ou factions : allié, ennemi, famille, amour, rival, mentor, hiérarchie, affaires, dette, autre ; libellé libre, détails, sens unique ou réciproque, relation secrète.
- Survol d'un point : mise en évidence de son voisinage et de son accroche. Clic : ouverture de la fiche.
- Création rapide : double-clic sur la carte, ou boutons PNJ / Faction. Mode "Lier" : cliquer deux fiches pour créer une relation.
- Recherche instantanée (touche / ou Ctrl+K) sur nom, fonction, accroche, étiquettes.
- Filtres : types de relation (légende cliquable), factions, PNJ morts, relations secrètes, liens d'appartenance.
- Textes en Markdown, avec liens entre fiches : [[Nom du PNJ]]. Le HTML glissé dans ces textes se limite à celui de l'éditeur (gras, mentions, encadrés, alignements, tableaux, images de la fiche, commentaires) et à quelques balises de mise en forme sans risque (surlignage, touche, abréviation, liste de définitions, légende de tableau ou de figure, largeur d'une image…) : le reste (classes, styles, identifiants, dialogues, liens `//hôte`…) est retiré à l'affichage.
- Plusieurs campagnes.
- **Formulaire d'une fiche** (PNJ, faction, lieu) : seuls les champs modifiés sont enregistrés (un champ complété entre-temps par Claude ou un autre MJ n'est pas remis à l'ancienne valeur). Si la fiche a changé depuis l'ouverture du formulaire, les changements des autres sur d'autres champs sont repris d'eux-mêmes ; un champ modifié des deux côtés est signalé en haut du formulaire, avec la version actuelle : garder sa saisie ou reprendre la leur, rien n'est enregistré avant ce choix et la saisie n'est jamais perdue (le texte remplacé reste dans l'historique de la fiche). Quitter une saisie non enregistrée (autre fiche, fond de la carte, autre onglet de Maze, fermeture de l'onglet du navigateur) demande confirmation.

## Recherche globale

- **Ctrl+K** (ou le champ « Rechercher » en haut de l'écran ; Ctrl+Maj+F fonctionne aussi dans l'éditeur, où Ctrl+K crée un lien) : PNJ, factions, fiches rédigées (titre et texte) et commentaires.
- Accents et majuscules ignorés, un début de mot suffit ; résultats regroupés par type avec l'extrait trouvé surligné, navigation au clavier.
- Un résultat ouvre la fiche PNJ ou faction sur la carte, la fiche rédigée sur le passage trouvé (surligné quelques secondes) ou le fil de commentaires.
- Portée : la campagne en cours ou toutes ses campagnes. Index plein texte SQLite (FTS5) tenu à jour automatiquement.

## Filtrer la carte sur un scénario

- Bouton **Scénario** de la carte, ou bouton **Sur la carte** d'une fiche rédigée : seuls les PNJ et factions cités (@) dans la fiche et ses sous-pages restent affichés, avec leurs liens.
- Option **Voisins directs** : ajoute, en retrait, les fiches liées à celles qui sont citées. Le filtre figure dans l'adresse (#/c/1?scenario=12) : on peut le garder en favori.

## Module Lieux

- Onglet **Lieux** : arborescence libre (région > ville > quartier > bâtiment > salle, donjon, nature…) à gauche, fiche du lieu au centre. Création d'un lieu ou d'un sous-lieu en deux champs, type proposé d'après le parent ; ordre modifiable, déplacement d'une branche par « Rangé dans ». Supprimer un lieu fait remonter ses sous-lieux d'un niveau.
- Fiche : accroche, texte d'ambiance à lire aux joueurs, description, secrets du MJ, notes (Markdown, liens [[Nom]] vers PNJ, factions et lieux), étiquettes, images (la première sert d'illustration).
- **PNJ et factions présents**, chacun avec un rôle (« Vit ici », « Travaille ici », « Se cache ici », « Siège », « Contrôle »… ou texte libre), depuis la fiche du lieu ou depuis le panneau du PNJ / de la faction sur la carte (pastilles « Lieux »). La fiche liste aussi ceux des sous-lieux, et les scénarios qui citent le lieu.
- **Sur la carte** : affiche uniquement les PNJ et factions du lieu et de ses sous-lieux (bandeau, voisins directs en option).
- Dans les scénarios, **@** propose aussi les lieux, et « @Nouveau nom » permet de créer un lieu à la volée. Les lieux sont dans la recherche globale, l'export / import de campagne et les sauvegardes.
- À la mise à jour, l'ancien champ « Lieu » des PNJ (texte libre, jamais affiché) devient un vrai lieu relié au PNJ.

## Module Scénarios

- Fiche de campagne (page d'accueil des scénarios) et scénarios en sous-fiches, eux-mêmes découpables en sous-pages (actes, scènes, annexes). Ordre modifiable, statut par scénario (Brouillon, Prêt, En cours, Joué).
- Éditeur visuel : titres, gras, italique, souligné, barré, listes, citation, séparateur, liens, images (bouton, collage ou glisser-déposer), tableaux, annuler / rétablir.
- **Tableaux** (bouton de la barre d'outils ou « / Tableau ») : ligne d'en-tête, Tab / Maj+Tab pour passer d'une cellule à l'autre (Tab dans la dernière cellule ajoute une ligne), colonnes redimensionnables à la souris. Une barre au-dessus du tableau ajoute ou supprime lignes et colonnes, fusionne ou scinde des cellules.
- **Modèles** : à la création d'un scénario ou d'une sous-page, choix d'un modèle (Scénario, Scène, Lieu, Préparation de séance, ou page vierge). Toute fiche peut être enregistrée comme modèle (menu ⋯ de la fiche) : il est alors proposé à tous les MJ de l'instance (choix assumé, signalé par le champ `shared` de l'API, et dans l'interface : liste des modèles, fenêtre « Enregistrer comme modèle ») ; les mentions y deviennent du texte, images et commentaires ne sont pas repris.
- **Duplication** d'une fiche avec ses sous-pages (menu ⋯ de la fiche ou de l'arbre) : la copie, en brouillon, est placée juste après l'original ; les commentaires et l'historique ne sont pas copiés.
- **Historique des versions** (onglet Historique du panneau de droite) : Maze garde une version au début et à la fin de chaque séance d'écriture, et avant chaque restauration ; on peut aussi nommer l'état actuel (« avant la séance 4 »). Un clic affiche une version en lecture ; « Restaurer » remet son contenu pour tous les MJ (l'état d'avant reste dans l'historique). Les 100 dernières versions automatiques sont gardées par fiche ; les versions nommées et les repères (« Avant restauration », « Avant modification par Claude »…) sans limite.
- Raccourcis à la frappe : « # », « ## », « ### » (titres), « - » (liste), « 1. » (liste numérotée), « > » (citation), « --- » + Entrée (séparateur). Menu « / » en début de ligne pour insérer un bloc.
- Encadrés JDR : « Accroche » (texte à lire aux joueurs) et « Secret » (réservé au MJ, masqué à l'impression).
- Mentions : « @ » propose les PNJ et factions ; « @Nouveau nom » permet de créer la fiche à la volée. Les mentions suivent les renommages. Un clic affiche un aperçu et un lien vers la fiche sur la carte ; les fiches PNJ et faction listent les pages qui les citent (« Apparaît dans »).
- **Commentaires** : sélectionner un passage puis cliquer sur la bulle dans la marge (ou Ctrl+Alt+M). Les fils apparaissent dans l'onglet Commentaires du panneau de droite, dans l'ordre du texte ; réponses, modification et suppression de ses messages, résolution et réouverture. Le passage commenté est surligné et suit les modifications de tous les MJ ; cliquer dessus ouvre le fil. Tout se met à jour en direct chez les autres MJ.
- Table des matières automatique avec suivi de la section en cours, enregistrement automatique, mode lecture pour la partie, impression propre (Ctrl+P).
- **Édition à plusieurs en temps réel** : plusieurs MJ peuvent écrire dans la même fiche au même moment, comme dans un document partagé. Chacun voit le curseur et la sélection des autres avec leur nom, et les pastilles en haut de la fiche indiquent qui est présent. En cas de coupure réseau, on continue d'écrire : les modifications sont envoyées à la reconnexion et fusionnées automatiquement. Le titre et le statut se mettent aussi à jour chez les autres.
- **Modifications qui ne peuvent pas être fusionnées** : si la fiche a été remplacée pendant la coupure (restauration d'une version ou d'une sauvegarde, modification par Claude, image supprimée), si une modification dépasse 2 Mo (collage géant), si la fiche a été supprimée ou l'accès retiré, le texte tel qu'il était dans le navigateur est gardé dans l'historique de la fiche (version « Modifications hors ligne de <nom> »), ou à défaut dans le navigateur (boutons Copier et Télécharger), même si l'on a quitté la fiche entre-temps. Un avis reste affiché jusqu'à ce qu'on le ferme, y compris après un rechargement. Sur un poste partagé, un texte gardé dans le navigateur n'est montré qu'à son compte une fois connecté (jamais sur l'écran de connexion ni à un autre compte), il est de nouveau envoyé dans l'historique à la connexion suivante de ce compte puis retiré du navigateur, la déconnexion avertit s'il en reste (enregistrer, télécharger ou abandonner), et il est effacé au bout de 7 jours (date indiquée dans l'avis).
- **Mise à jour de Maze pendant qu'un onglet est ouvert** : si elle change la structure des fiches (nouveau type de bloc, nouvel attribut), un onglet resté sur l'ancienne interface ne peut plus se connecter aux fiches (il supprimerait chez tous les MJ les blocs qu'il ne connaît pas) : il garde son texte non envoyé comme ci-dessus et se recharge de lui-même ; si un formulaire de fiche a une saisie en cours, un bandeau invite à la recopier puis à recharger. Les mises à jour qui ne touchent pas à la structure des fiches ne déconnectent personne.

## Export et import d'une campagne

- Menu de la campagne (en haut à gauche) > **Exporter** : archive `.zip` contenant `campaign.json` (PNJ, factions, appartenances, liens et leurs natures, positions sur la carte, fiche de campagne, scénarios et sous-pages) et les images.
- **Importer une campagne** (même menu, écran d'accueil ou Administration > Campagnes) : crée une nouvelle campagne, attribuée à la personne qui l'importe ; si le nom est déjà pris par une campagne qu'elle voit, il est suffixé « (importée) » (les campagnes des autres comptes ne comptent pas : rien ne révèle leurs noms). Les identifiants sont renouvelés et les références réécrites (liens, mentions @, images dans les fiches).
- Accessible aux MJ pour leurs propres campagnes. L'historique d'édition en temps réel et l'historique des versions ne sont pas exportés : les fiches repartent de leur dernier texte enregistré. Les modèles de fiches (communs à l'instance) non plus ; ils sont en revanche dans les sauvegardes complètes.
- Garde-fous à l'import : `campaign.json` de 25 Mo au plus (une campagne de 1000 PNJ en fait 1,3 Mo ; l'export prévient avant de produire une archive qui dépasserait cette limite, et donc ne pourrait pas être réimportée), lu sans croire la taille annoncée par l'archive ; archive refusée si `campaign.json` est anormalement compressé (plus de 100 fois au-delà de 1 Mo : une vraie campagne l'est 4 à 40 fois) ou contient plus de 500 000 objets ; images de 50 Mo au plus.

## Comptes et administration

- Connexion par identifiant et mot de passe (haché avec scrypt). Changer son mot de passe ferme ses autres sessions.
- Essais de mot de passe (connexion et autorisation du connecteur Claude) : 8 en 10 minutes par adresse, puis refus temporaire. Par compte, un délai croissant (0,5 s à partir du 4e essai sans succès, puis 1, 2, 4, 8 et 10 s au plus), compté en créneaux : des essais envoyés en même temps, même depuis de nombreuses adresses, sont vérifiés l'un après l'autre, et ceux qui devraient attendre plus de 10 s sont refusés aussitôt (429, sans compter d'échec) ; un flot d'essais n'obtient ainsi qu'une vérification toutes les 10 s. Sans bloquer l'administrateur : une adresse qui s'est connectée au compte dans les 30 derniers jours (gardée en base, 32 par compte au plus, redémarrages compris) a ses propres créneaux et passe au pire après son propre délai, quoi que fassent les autres adresses. Une adresse nouvelle (appareil jamais utilisé) n'est pas distinguable d'un essai de l'attaque ; quand les créneaux du compte sont pris, elle obtient un essai unique hors file (s'il n'y a pas eu d'essai depuis cette adresse sur ce compte dans les 10 dernières minutes ; compté comme un échec avant d'être vérifié, 2 au plus en attente par compte), vérifié au plus 10 s après : une attaque venue de quelques adresses ne la bloque pas ; seule une attaque qui renouvelle sans cesse ses adresses peut la faire attendre, jusqu'à 10 s après sa fin. Les refus disent combien de temps attendre (« Réessayez dans 6 minutes » pour l'adresse, « Réessayez dans 8 secondes » pour un compte dont les créneaux sont pris) et l'en-tête Retry-After le donne en secondes. L'adresse est celle du client si `FORWARDED_ALLOW_IPS` contient celle du reverse proxy (par défaut, les réseaux privés et locaux ; voir Configuration).
- Deux rôles : **Administrateur** (toutes les campagnes, accès à l'administration) et **MJ** (uniquement les campagnes qui lui sont attribuées ; une campagne créée par un MJ lui est attribuée).
- Premier démarrage : si aucun compte n'existe, un compte `admin` est créé avec le mot de passe du secret Docker (MAZE_PASSWORD_FILE). Sans secret, l'interface propose de créer le premier administrateur.
- Administration (menu du compte > Administration) :
  - **Comptes** : création, rôle, campagnes attribuées, désactivation, réinitialisation du mot de passe, suppression.
  - **Campagnes** : vue globale, attribution aux MJ, renommage, export, import, suppression.
  - **Sauvegardes** : sauvegarde immédiate, export (sauvegarde téléchargée aussitôt), import d'une archive, restauration (l'état actuel est d'abord sauvegardé en « avant-restauration »), téléchargement, suppression.
  - **Mise à jour** : version en service, modifications disponibles sur GitHub, mise à jour en un clic, journal.

## Langue de l'interface

L'interface et les messages du serveur existent en français et en anglais.

- Choix de la langue, dans cet ordre : réglage du compte (menu du compte > Langue : Automatique, Français, English ; gardé par le compte, sur tous les appareils), puis choix fait sur l'écran de connexion (petit sélecteur sous le bouton, gardé dans ce navigateur), puis langue du navigateur (la première que Maze connaît), puis `MAZE_DEFAULT_LOCALE` (`fr` par défaut, voir Configuration). « Automatique » suit l'écran de connexion et le navigateur. Le changement s'applique aussitôt, sans recharger la page.
- Les messages d'erreur du serveur suivent la même langue, temps réel compris (l'interface transmet sa langue à la connexion).
- Données de départ : les natures de lien, les types de lieux et les titres par défaut sont créés dans la langue de la personne qui crée ou importe la campagne, puis ne changent plus : ce sont des données de la campagne, modifiables comme les autres. Les données saisies par les MJ ne sont jamais traduites. Campagne de démonstration en anglais : `python -m maze.demo --lang en`.
- Foundry VTT : la fenêtre du module suit la langue du Foundry de chaque MJ ; les pages composées par Maze, les dossiers et les noms créés dans le monde suivent la langue des pages du monde, la même pour tous les MJ : réglage du module « Langue des pages importées » (Configurer les paramètres > Maze Sync ; Automatique, Français, English). En « Automatique » : langue retenue au premier import du monde (ou au dernier fait avec une langue choisie dans ce réglage), français pour un monde déjà importé avant ce réglage (ses pages restent tenues pour inchangées), sinon langue du Foundry du MJ qui fait ce premier import. Changer ce réglage renomme les dossiers et réécrit à l'import suivant les pages non modifiées dans Foundry ; les autres sont signalées (« Modifiés dans Foundry »).
- Restent en français : les outils du connecteur Claude (noms, descriptions et messages lus par Claude, qui répond dans la langue de la conversation) et son journal, ce guide et `docs/ARCHITECTURE.md`, les journaux du serveur, les valeurs internes (clés des natures de lien et des types de lieux, statuts, origines des sauvegardes comme `quotidienne` dans `MAZE_BACKUP_KEEP`).
- Ajouter une langue : `frontend/src/i18n/README.md`, section « Adding a language » (en anglais).

## Stack

| Couche | Choix | Raison |
|---|---|---|
| Serveur | Python 3.13, Starlette, Pydantic, Uvicorn | Léger, peu de dépendances, API REST simple |
| Base de données | SQLite (mode WAL) | Un seul conteneur, sauvegarde = un fichier, largement suffisant pour un usage MJ + quelques joueurs |
| Images | Pillow | Conversion WebP, redimensionnement (1600 px) et miniature carrée (256 px) |
| Interface | React 19, esbuild | Build en une seconde, aucun outil lourd |
| Graphe | Cytoscape.js + fcose | Graphe performant, images dans les nœuds, disposition automatique |
| Éditeur | ProseMirror + Yjs (y-prosemirror) | Édition à plusieurs : chaque navigateur fusionne les modifications (CRDT), le serveur les enregistre et les relaie par WebSocket |

Une seule image Docker (build multi-étapes), un seul volume `/data` contenant `maze.db`, les images (`media/`) et les sauvegardes (`backups/`).

## Déploiement (serveur avec Docker et Caddy)

Exemple avec le dépôt cloné dans /opt/maze et Caddy en reverse proxy HTTPS sur maze.example.org (à remplacer par le nom réel).

Installation complète sur un Debian neuf, avec Caddy sur une autre machine : deploy/installation-debian.md.

### 1. Clé de déploiement GitHub (lecture seule)

Sur le serveur :

    ssh-keygen -t ed25519 -f ~/.ssh/maze_deploy -N "" -C "deploy maze@$(hostname)"
    cat ~/.ssh/maze_deploy.pub

Ajouter cette clé publique dans le dépôt GitHub : Settings > Deploy keys > Add deploy key, sans cocher "Allow write access". Puis ajouter à ~/.ssh/config :

    Host github-maze
        HostName github.com
        User git
        IdentityFile ~/.ssh/maze_deploy
        IdentitiesOnly yes

### 2. Récupération du code

    sudo mkdir -p /opt/maze && sudo chown "$USER": /opt/maze
    git clone git@github-maze:ranglier/Maze.git /opt/maze
    cd /opt/maze
    cp .env.example .env

Dans .env, MAZE_BIND vaut 127.0.0.1 si Caddy est sur la même machine, sinon l'IP privée de la machine Maze. FORWARDED_ALLOW_IPS désigne le reverse proxy dont Maze croit les en-têtes X-Forwarded-For (adresse réelle des clients, pour la limite des essais de connexion) et X-Forwarded-Proto (adresses https du connecteur Claude). Laissée vide, elle vaut les réseaux privés et locaux (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16), où Caddy est toujours vu par Maze : son IP privée s'il est sur une autre machine (la redirection de port de Docker la conserve), la passerelle du réseau Docker (172.x.0.1) s'il est sur la même. Rien n'est donc à régler pour que cela fonctionne. Recommandé toutefois : l'IP exacte de Caddy (par exemple 10.0.0.20 s'il est sur une autre machine du réseau privé), car avec la valeur par défaut un appareil du réseau local qui joindrait directement le port de Maze pourrait choisir son adresse apparente (les créneaux par compte s'appliquent quand même, mais il pourrait se faire passer pour l'adresse habituelle de l'administrateur, ou inventer des adresses neuves pour prendre les deux essais uniques hors file). Caddy sur la même machine : l'IP de la passerelle (docker network inspect maze_default, champ Gateway), et non 127.0.0.1. Un proxy hors de ces réseaux est signalé dans Administration > Mise à jour.

### 3. Mot de passe (secret Docker)

Saisie masquée, à lancer seule (la longueur s'affiche pour confirmer) :

    read -rs -p "Mot de passe Maze : " MAZE_PW && echo " - saisi (${#MAZE_PW} caractères)"

Puis :

    mkdir -p secrets
    printf '%s' "$MAZE_PW" > secrets/maze_password.txt
    unset MAZE_PW
    chmod 600 secrets/maze_password.txt
    sudo chown 1000:1000 secrets/maze_password.txt

Le conteneur tourne avec l'UID 1000, d'où le chown. Le dossier secrets/ est ignoré par git : les mises à jour n'y touchent pas.

### 4. Premier lancement

    docker compose up -d --build
    docker compose logs -f maze

Campagne de démonstration (facultatif ; `--lang en` pour une campagne en anglais, défaut : `MAZE_DEFAULT_LOCALE`) :

    docker compose exec maze python -m maze.demo

### 5. Caddy

DNS : enregistrement A maze.example.org vers l'IP publique du serveur qui héberge Caddy. Ajouter au Caddyfile le bloc de deploy/Caddyfile.maze, puis :

    sudo caddy validate --config /etc/caddy/Caddyfile
    sudo systemctl reload caddy

Maze n'écoute que sur l'adresse MAZE_BIND : il n'est joignable que via Caddy. Le cookie de session est réservé à HTTPS (MAZE_COOKIE_SECURE=true) : la connexion ne fonctionne donc pas en accès direct http://127.0.0.1:8000.

Le bloc fourni laisse passer les imports d'archives jusqu'à 4 Gio (MAZE_MAX_IMPORT_MB) et les autres requêtes jusqu'à 410 Mio (le plus gros envoi d'images admis par Maze) : Maze applique ensuite ses propres limites, plus fines (voir « Limites des requêtes »). Un ancien bloc à 60 Mo refuse les imports de sauvegardes plus grosses (erreur 413 de Caddy) : le remplacer.

Si Caddy tourne lui-même dans un conteneur, remplacer dans le bloc reverse_proxy 127.0.0.1:8000 par reverse_proxy maze:8000, et brancher Maze sur le réseau de Caddy avec un fichier docker-compose.override.yml (ignoré par les mises à jour car absent du dépôt) :

    services:
      maze:
        ports: !reset []
        networks: [caddy]
    networks:
      caddy:
        external: true

(remplacer caddy par le nom réel du réseau, visible avec docker network ls). FORWARDED_ALLOW_IPS : la valeur par défaut couvre ce réseau Docker ; pour la restreindre, son sous-réseau (docker network inspect caddy, champ Subnet, par exemple 172.18.0.0/16).

### Mises à jour

    cd /opt/maze && ./deploy/update.sh

Le script récupère le code, garde l'image en service sous l'étiquette maze:previous, construit la nouvelle, sauvegarde la base (archive `-maj`) juste avant de redémarrer, puis vérifie que Maze répond. Il compare la version en service (MAZE_VERSION du conteneur) au dernier commit : « Déjà à jour » seulement si c'est la même (FORCE=1 pour reconstruire quand même). Ainsi, après une sauvegarde refusée ou une construction ratée, la relance reconstruit bien. Une seule mise à jour à la fois : lancé pendant une autre (bouton de l'administration en cours, par exemple), le script s'arrête aussitôt sans rien faire (verrou .git/maze-update.lock).

En cas d'échec :
- avant le redémarrage (récupération du code, construction, sauvegarde refusée) : l'ancienne version reste en service et le code est remis à sa version ;
- si la nouvelle version ne répond pas dans le délai HEALTH_TIMEOUT (180 s par défaut ; le temps des migrations de la base ne compte pas, voir plus bas) ou si son conteneur s'arrête ou redémarre en boucle (échec aussitôt constaté) : retour automatique à la précédente. Le conteneur est arrêté, le code remis au commit d'avant, maze:previous relancée. Si la nouvelle version avait migré la base (version du schéma changée), l'ancien code ne la connaît pas : la sauvegarde `-maj` faite juste avant le redémarrage est d'abord restaurée, application arrêtée. La base migrée n'est pas perdue : la restauration la garde dans une archive `-avant-restauration` (ce qui a été écrit entre la sauvegarde et le retour n'est que là). Cette restauration est exacte (`maze.backup --restore … --exact`, dès que l'image relancée la connaît) : la sauvegarde n'ayant que quelques minutes, comptes, sessions et jetons du connecteur Claude et de Foundry sont ceux de l'archive, rien n'est révoqué. Avec une image plus ancienne (sans `--exact`), c'est une restauration ordinaire : sessions fermées, jetons du connecteur Claude (le reconnecter) et de Foundry (en recréer un dans Administration et le recopier dans chaque monde) révoqués. Base non migrée : gardée telle quelle ;
- si la base migrée ne peut pas être remise en l'état (restauration en échec, ou pas de sauvegarde `-maj`), ou si sa version est devenue illisible alors que celle d'avant la mise à jour était connue (base peut-être migrée), Maze est laissé ARRÊTÉ plutôt que relancé sur une base que l'ancien code ne connaît pas ; il n'y a alors plus d'interface : le résultat est dans la sortie du script, dans /opt/maze/status/update.log et dans le journal du service (journalctl -u maze-updater). Au choix, dans /opt/maze : revenir à l'ancienne version en restaurant à la main la sauvegarde d'avant mise à jour avec l'image d'avant, déjà étiquetée maze:latest (`docker compose run --rm --no-deps -T maze python -m maze.backup --restore /data/backups/maze-…-maj.tar.gz --exact`, puis `docker compose up -d --no-build`), ou relancer la nouvelle version sur la base migrée une fois l'échec compris (`git reset --keep NOUVEAU_COMMIT && MAZE_VERSION=NOUVEAU_COMMIT_ABRÉGÉ docker compose up -d --build`). Le script affiche ces commandes avec le nom de l'archive et le commit, et la commande qui liste les archives ;
- si le retour échoue autrement (ou si rien n'était en service avant), le script s'arrête avec les journaux : intervention nécessaire.

Migrations longues : pendant que la nouvelle version migre la base, elle pose le fichier /data/.migration-en-cours ; le script attend alors la fin sans décompter HEALTH_TIMEOUT, dans la limite de MIGRATION_TIMEOUT (1200 s par défaut). Lancé par le bouton, le script est arrêté par systemd au bout du TimeoutStartSec de l'unité installée (30 min dans deploy/systemd, lu avec systemctl) : l'attente d'une migration s'arrête alors assez tôt pour garder ROLLBACK_RESERVE (600 s) au retour arrière, qui ne doit pas être interrompu en pleine restauration. Lancé à la main, pas d'autre limite que MIGRATION_TIMEOUT. Par exemple, pour une mise à jour lancée à la main sur une petite machine : `HEALTH_TIMEOUT=300 ./deploy/update.sh`.

Codes de sortie (repris par le bouton de l'administration) : 0 réussite ou déjà à jour, 1 échec avant le redémarrage, 3 retour automatique à la version précédente, 4 intervention nécessaire, 5 intervention nécessaire avec Maze laissé arrêté (base migrée non remise en l'état). Retour manuel à la version d'avant la dernière mise à jour réussie (maze:previous est gardée jusqu'à la suivante) :

    git reset --keep COMMIT_D_AVANT && docker image tag maze:previous maze:latest && docker compose up -d --no-build --force-recreate

(restaurer aussi la dernière archive `-maj` si la mise à jour avait migré la base, voir Sauvegarde et restauration). Le retour automatique ne vaut qu'à partir de la mise à jour qui suit son installation : celle qui l'installe est encore conduite par l'ancien script.

Le script refuse de démarrer s'il reste moins de 1,5 Go libres (variable MIN_FREE_MB) et fait le ménage après chaque mise à jour réussie (images sans étiquette, cache de build inutilisé depuis 24 h ; maze:previous, l'image d'avant, reste : compter une image de plus sur le disque). Prévoir un disque d'au moins 10 Go ; la taille par défaut d'une image Debian cloud (3 Go) ne suffit pas à reconstruire l'image.

Base plus récente que le code : Maze refuse de démarrer (journal « Démarrage refusé, … ») sur une base qui contient une migration qu'il ne connaît pas (base migrée par une version plus récente ou par une autre branche, migrations enregistrées par nom dans la table `schema_migrations`), ou si deux fichiers de migration portent le même numéro. Restaurer alors la sauvegarde `-maj` d'avant la mise à jour, ou relancer la version qui a migré la base ; ne jamais relancer un ancien code sur une base migrée (`git checkout` d'un commit antérieur suivi d'un `docker compose up --build`) sans restaurer d'abord.

### Verrou des dépendances de l'interface

La construction de l'interface doit être reproductible (audit M21) : `frontend/package-lock.json` fige toutes les versions (dépendances transitives comprises, avec leur empreinte) et le Dockerfile et l'intégration continue l'installent avec `npm ci`. Les images de base du Dockerfile s'épinglent par empreinte, en gardant le tag pour la lecture (`FROM node:22-alpine@sha256:…`). Tant que le verrou n'est pas dans le dépôt, la construction fait `npm install` avec un avertissement (`AVERTISSEMENT : frontend/package-lock.json absent`).

Produire ou mettre à jour le verrou et les empreintes, au choix :
- GitHub, Actions > « Verrou npm et images » (`.github/workflows/lockfile.yml`, lancé à la main par « Run workflow » une fois sur la branche par défaut, et à chaque envoi d'une branche `audit-lot4-*`) : artefact `verrou-et-images` avec `package-lock.json` (fait par le npm de l'image node du Dockerfile, vérifié contre package.json), `images.txt` (lignes `tag@sha256:…` à recopier dans les `FROM`) et `versions.txt` ;
- ou sur un poste avec Node 22 (npm 10), une seule commande dans le dépôt :

      cd frontend && npm install --package-lock-only --no-audit --no-fund

  (ou, avec Docker et exactement le npm de l'image : `docker run --rm -v "$PWD/frontend:/w" -w /w node:22-alpine npm install --package-lock-only --no-audit --no-fund`) ; empreintes : `docker buildx imagetools inspect node:22-alpine` et `python:3.13-slim` (ligne `Digest:`).

Puis vérifier (`python3 .github/scripts/check_lockfile.py` : versions identiques à package.json), commiter `frontend/package-lock.json` et le Dockerfile ; la CI les reconstruit avec `npm ci`. Une mise à jour d'une dépendance : changer sa version exacte dans package.json, refaire le verrou, commiter les deux.

### Mise à jour depuis l'interface (service maze-updater)

Le conteneur n'a volontairement pas accès à Docker (ce serait un accès root à la machine depuis le web). Le bouton « Mettre à jour » dépose une demande dans /opt/maze/control ; un service systemd de l'hôte la détecte, exécute deploy/update.sh sous l'utilisateur ops et publie l'état et le journal dans /opt/maze/status. Un timer vérifie aussi chaque nuit les modifications disponibles.

Les deux dossiers sont à sens unique. control/ est le seul où le conteneur peut écrire, et il n'y dépose que sa demande (control/request) : le service n'y écrit jamais, il en retire la demande par un renommage (qui ne suit pas les liens symboliques) et refuse tout ce qui n'est pas un fichier ordinaire. status/ n'est écrit que par ops et monté en lecture seule dans le conteneur. Un code malveillant qui aurait pris la main dans le conteneur ne peut donc pas, par un lien symbolique posé dans control/, faire écrire le service dans un fichier de l'hôte (deploy/update.sh par exemple, exécuté ensuite par ops).

Le service tourne en ops, membre du groupe docker : cela équivaut à un accès root à la machine. Ce n'est acceptable que pour un compte d'administration réservé à cet usage. Pour réduire ce risque, Docker peut tourner sans root (mode rootless : https://docs.docker.com/engine/security/rootless/) ; ce n'est pas nécessaire au fonctionnement de Maze.

Installation (une fois, en ops sur la machine Maze) :

    cd /opt/maze
    install -d -m 2775 control
    id -u ops

Si l'UID affiché n'est pas 1000 (utilisateur du conteneur), donner le groupe 1000 au dossier : sudo chgrp 1000 control

Le dossier status/ fait partie du dépôt (créé par git, donc appartenant à ops) : rien à faire. S'il a disparu, deploy/update.sh le recrée avant de redémarrer Maze (sinon Docker le créerait en root et le service ne pourrait plus y écrire).

Si control/ a disparu, deploy/update.sh le recrée avec le groupe du conteneur (1000, ou la variable MAZE_GID si l'image utilise un autre gid). Si ops n'est pas membre de ce groupe, le script le crée quand même, poursuit la mise à jour et affiche la commande sudo chgrp à lancer. Si control/ existe déjà avec un autre groupe, le script le signale à chaque mise à jour, avec la même commande.

    sudo cp deploy/systemd/maze-updater.service deploy/systemd/maze-updater.path deploy/systemd/maze-updater.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now maze-updater.path maze-updater.timer
    sudo systemctl start maze-updater.service
    cat status/state status/info

Les unités supposent le dépôt dans /opt/maze et l'utilisateur ops : adapter User= et les chemins sinon. Journal du service : journalctl -u maze-updater -n 50. Elles ne sont copiées qu'à l'installation : si celles du dépôt changent un jour, deploy/update.sh le signale à chaque mise à jour avec la commande sudo cp à relancer.

Si le service s'arrête sans pouvoir écrire son résultat (disque plein, redémarrage, arrêt forcé), l'interface le détecte (verrou status/.lock libéré) et affiche « Opération interrompue » ; les boutons redeviennent utilisables.

Le service exécute deploy/update.sh (voir Mises à jour) et affiche son résultat : réussite, échec avant le redémarrage (ancienne version toujours en service), retour automatique à la version précédente, ou intervention nécessaire, avec le journal.

Les versions précédentes du service écrivaient leur état dans control/. À la mise à jour qui installe cette version, l'ancien service (déjà lancé) termine son travail et y écrit son résultat, que l'interface lit tant que status/ est vide ; le nouveau service reprend ce résultat au passage suivant (vérification nocturne ou bouton) et vide control/. Aucune manipulation n'est nécessaire.

## Configuration

| Variable | Défaut | Rôle |
|---|---|---|
| MAZE_PASSWORD_FILE / MAZE_PASSWORD | (vide) | Mot de passe du compte `admin` créé au premier démarrage (ignoré ensuite) |
| MAZE_CONTROL_DIR | (vide, /control dans le compose) | Dossier où le conteneur dépose ses demandes au service maze-updater |
| MAZE_STATUS_DIR | (vide, /status dans le compose, en lecture seule) | État publié par le service maze-updater (à défaut : MAZE_CONTROL_DIR, ancien emplacement) |
| FORWARDED_ALLOW_IPS | réseaux privés et locaux (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, ::1, fc00::/7) | Adresses (ou sous-réseaux, virgules) du reverse proxy dont Maze croit les en-têtes X-Forwarded-For et X-Forwarded-Proto ; recommandé dans .env : l'IP exacte de Caddy (par exemple 10.0.0.20 ; voir Récupération du code). Lue par uvicorn ; jamais `*` (l'adresse serait alors choisie par le client) |
| MAZE_VERSION | dev | Version affichée (renseignée par update.sh au build) |
| MAZE_SECRET_KEY(_FILE) | générée dans /data/.secret_key | Clé de signature des cookies de session |
| MAZE_COOKIE_SECURE | false (true dans le compose) | Cookie de session limité à HTTPS |
| MAZE_SESSION_DAYS | 30 | Durée de la session |
| MAZE_MAX_UPLOAD_MB | 20 | Taille maximale par image |
| MAZE_MAX_IMPORT_MB | 4096 | Taille maximale d'une archive importée (Administration > Sauvegardes, import d'une campagne) |
| MAZE_BACKUP_KEEP | (vide : quotidienne=14, manuelle=3, maj=3, avant-restauration=3, import=2) | Archives conservées par nature, ex. `quotidienne=30,maj=5` (voir Sauvegarde et restauration) |
| MAZE_DATA_DIR | /data | Base, médias, sauvegardes |
| MAZE_STATIC_DIR | /app/static dans l'image (hors image : `backend/maze/static`) | Interface construite servie par Maze (`frontend/dist` en développement) |
| MAZE_DB_WRITE_THREADS / MAZE_DB_READ_THREADS | 5 / 11 | Fils réservés à la base, pour les écritures et pour les lectures (des enregistrements qui attendent un verrou ne retardent pas les lectures). Pas dans le compose : à ajouter sous `environment:` pour les changer |
| MAZE_PUBLIC_URL | (vide : déduite des requêtes) | Adresse publique, ex. https://maze.example.org : adresses OAuth et URL du serveur MCP du connecteur Claude |
| MAZE_DEFAULT_LOCALE | fr | Langue de l'interface et des messages du serveur quand ni le compte (menu du compte > Langue : Automatique, Français, English), ni le choix fait sur l'écran de connexion, ni le navigateur ne demandent une langue que Maze connaît : `fr` ou `en`. Sert aussi aux données de départ créées sans autre indication (`python -m maze.demo`) et aux pages de Foundry quand le module demande une langue que le serveur ne connaît pas. Voir Langue de l'interface. Dans `.env` |
| MAZE_SOURCE_URL | https://github.com/ranglier/Maze | Adresse du code source de la version en service : lien « Code source (AGPL-3.0) » de l'écran de connexion et du menu du compte (AGPL, section 13). Une version modifiée doit y mettre l'adresse de ses propres sources. Dans `.env` |
| MAZE_MAP_FONT | (vide) | Fichier de police TrueType des numéros et noms de l'aperçu d'une carte interactive envoyé à Claude (outil `view_map` du connecteur) ; essayé avant DejaVu Sans (installée par le Dockerfile, paquet fonts-dejavu-core), elle-même avant la police de Pillow, qui n'a pas les lettres accentuées ; fichier absent ou illisible : police suivante. Lue au démarrage. Pas dans le compose : utile seulement hors de l'image, sans DejaVu |
| MAZE_LIVE_MAX_PER_USER / MAZE_LIVE_MAX_TOTAL | 12 / 100 | Connexions d'édition en temps réel ouvertes en même temps, par compte (toutes fiches) et pour toute l'instance ; au-delà, la fiche affiche la raison du refus à la place de « Connexion… » et le navigateur réessaie plus tard (fermeture 4429). Pas dans le compose : à ajouter sous `environment:` pour les changer |

Variable du service de mise à jour de l'hôte (`deploy/maze-updater.sh`, lancé par systemd ; ni lue par le conteneur, ni prise dans `.env`) : `MAZE_BRANCH` (défaut `main`), branche dont la vérification compare les commits (« disponible », liste des commits) et que le bouton « Mettre à jour » installe (`deploy/update.sh <branche>`). Pour une autre branche : `sudo systemctl edit maze-updater.service`, puis sous `[Service]` la ligne `Environment=MAZE_BRANCH=<branche>`. `./deploy/update.sh` lancé à la main prend la branche en argument (`main` par défaut) et ignore `MAZE_BRANCH`.

Un seul serveur par dossier de données : Maze prend au démarrage un verrou sur `/data/.maze.lock` (rendu à l'arrêt, même brutal). Un second serveur sur le même dossier (`uvicorn --workers 2`, second conteneur sur le même volume) refuse de démarrer avec le message « Un autre serveur Maze utilise déjà le dossier de données » : l'édition en temps réel garde ses salles en mémoire, deux processus effaceraient chacun les modifications de l'autre. La sauvegarde et la restauration en ligne de commande (`python -m maze.backup`) restent possibles pendant que Maze tourne.

### Limites des requêtes

Maze coupe tout corps de requête trop gros dès la lecture (erreur 413, sans le garder en mémoire) :

| Requêtes | Taille maximale |
|---|---|
| Imports d'archives (sauvegarde, campagne) | MAZE_MAX_IMPORT_MB (4096 Mo) |
| Envoi d'images : galerie d'une fiche (10 fichiers), bibliothèque (20), illustration, fond de carte, icône (1) | MAZE_MAX_UPLOAD_MB (20 Mo) par fichier admis, plus 1 Mo |
| Textes longs : fiches rédigées, versions, modèles, PNJ, factions, lieux | 16 Mo |
| Connecteur Claude (/mcp) | 4 Mo, lots de 20 messages au plus |
| Connexion, création du premier compte, autorisation OAuth | 64 Ko |
| Autres requêtes (JSON) | 2 Mo |
| Message du temps réel (WebSocket) | 16 Mo (uvicorn, --ws-max-size) |

La création du premier compte (/api/auth/setup) répond 409 immédiatement dès qu'un compte existe, sans calcul du mot de passe. Les erreurs de validation (422) ne renvoient plus la valeur reçue. Un JSON invalide, imbriqué au-delà de la limite de récursion ou contenant NaN ou Infinity, est refusé en 400.

Arbres des fiches rédigées et des lieux : 32 niveaux au plus sous la fiche de campagne (ou sous un lieu à la racine). Créer ou déplacer plus bas est refusé (400 « Trop de niveaux de sous-pages », « Trop de niveaux de sous-lieux », message affiché par l'interface, rien n'est déplacé) ; à l'import d'une campagne, ce qui est plus profond est rangé au dernier niveau admis, sans perte.

## Sauvegarde et restauration

Sauvegarde à chaud (copie cohérente de la base + images), 14 sauvegardes quotidiennes conservées par défaut (`--keep N` pour en décider à l'appel) :

    docker compose exec maze python -m maze.backup

Chaque origine d'archive a sa propre rotation, d'après le nom (`maze-AAAAMMJJ-HHMMSS[-origine].tar.gz`, triées sur la date du nom) :

| Archive | Origine | Nature (`MAZE_BACKUP_KEEP`) | Conservées par défaut |
|---|---|---|---|
| `maze-….tar.gz` | ligne de commande, sauvegarde quotidienne (timer `maze-backup`) | `quotidienne` | 14 (ou `--keep`) |
| `maze-…-manuelle.tar.gz` | boutons « Sauvegarder maintenant » et « Exporter » de l'administration | `manuelle` | 3 |
| `maze-…-maj.tar.gz` | avant chaque mise à jour (`deploy/update.sh`, bouton « Mettre à jour ») | `maj` | 3 |
| `maze-…-avant-restauration.tar.gz` | état d'avant chaque restauration | `avant-restauration` | 3 (jamais celle qu'on restaure) |
| `maze-…-import.tar.gz` | archives importées dans l'administration | `import` | 2 |

Une série de mises à jour ou de restaurations ne chasse donc plus l'historique quotidien, et inversement. Chaque archive est une copie complète de la base et des images : hors des quotidiennes, peu sont gardées par défaut (3 suffisent à revenir sur les dernières mises à jour, sur les derniers instantanés manuels ou sur les deux dernières restaurations ; une archive importée l'est pour être restaurée aussitôt et son original existe ailleurs), soit 25 archives au plus. Pour changer ces nombres, variable `MAZE_BACKUP_KEEP` dans `.env` (« nature=nombre » séparés par des virgules, 0 = toutes ; les natures absentes gardent leur valeur par défaut ; un élément invalide est ignoré et signalé dans le journal), par exemple :

    MAZE_BACKUP_KEEP=quotidienne=30,maj=5,import=1

Elle est lue à chaque sauvegarde (administration, mise à jour, ligne de commande sans `--keep`) ; après l'avoir changée : `docker compose up -d`. Supprimer à la main les archives qui ne servent plus (Administration > Sauvegardes, qui affiche aussi la place prise par les archives et l'espace libre du volume).

Une sauvegarde ou une restauration est refusée (message clair, erreur 507 dans l'API, code 1 en ligne de commande) si le volume n'a pas au moins deux fois la taille de la dernière archive (ou de l'archive à restaurer, si elle est plus grosse), plus 200 Mo, d'espace libre : rien n'est alors écrit. Avant une mise à jour, ce refus arrête `deploy/update.sh` : l'ancienne version reste en service et le code est remis à sa version ; après avoir libéré de la place, relancer `./deploy/update.sh` ou le bouton « Mettre à jour ».

Les archives sont dans le volume, sous `/data/backups`. Pour les sortir :

    docker compose cp maze:/data/backups ./backups

Sauvegarde quotidienne sur l'hôte (3 h 15) : timer systemd fourni dans `deploy/systemd/` (une Debian minimale n'a pas de cron ; le timer rattrape une sauvegarde manquée si la machine était éteinte, et la sortie comme les erreurs vont dans le journal, sans besoin de courrier). Unités écrites pour ops et /opt/maze, à adapter sinon :

    sudo cp deploy/systemd/maze-backup.service deploy/systemd/maze-backup.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now maze-backup.timer
    sudo systemctl start maze-backup.service
    systemctl list-timers maze-backup.timer --no-pager
    journalctl -u maze-backup -n 20 --no-pager

La sauvegarde lancée à la main doit se terminer sans erreur (le journal donne le nom de l'archive) et le timer annoncer sa prochaine exécution. Avec cron, la ligne équivalente serait `15 3 * * * cd /opt/maze && docker compose exec -T maze python -m maze.backup >>$HOME/maze-sauvegarde.log 2>&1`. Une ligne cron existante qui contient `--keep 14` garde ce nombre de sauvegardes quotidiennes quel que soit `MAZE_BACKUP_KEEP` (`--keep` l'emporte) : la retirer, ou passer au timer. Administration > Sauvegardes affiche la date de la dernière sauvegarde quotidienne et avertit si elle a plus de 48 h (timer arrêté ou absent).

Archives faites avant la séparation par origine (lot 1 de l'audit, octobre 2026) : le bouton « Sauvegarder maintenant » de l'ancienne version et l'archive d'avant mise à jour de l'ancien `update.sh` n'avaient pas de suffixe. Rien dans leur nom ne les distingue d'une sauvegarde quotidienne : elles sont donc classées quotidiennes (origine « Quotidienne » dans Administration > Sauvegardes), comptent parmi les 14 quotidiennes gardées (la rotation les supprime avec les plus anciennes ; télécharger avant celles à garder) et peuvent être la « dernière sauvegarde quotidienne ». Sans sauvegarde quotidienne programmée, l'avertissement des 48 h reste alors muet jusqu'à 48 h après la plus récente d'entre elles. Vérifier donc que la sauvegarde quotidienne est bien programmée, au lieu de compter sur cet avertissement juste après la mise à jour :

    systemctl list-timers maze-backup.timer --no-pager

La nuit suivante, Administration > Sauvegardes doit montrer une archive quotidienne datée de 3 h 15 environ.

Restauration : depuis Administration > Sauvegardes (bouton Restaurer, après import de l'archive si elle vient d'ailleurs), ou en ligne de commande :

    docker compose cp ./maze-AAAAMMJJ-HHMMSS.tar.gz maze:/data/backups/
    docker compose exec maze python -m maze.backup --restore /data/backups/maze-AAAAMMJJ-HHMMSS.tar.gz
    docker compose restart maze

Maze peut tourner pendant la restauration : de l'interface comme en ligne de commande, les fiches ouvertes en édition sont fermées et plus rien de ce qu'on y tape n'est enregistré jusqu'à la fin (fichier `/data/.restauration-en-cours`, verrouillé pendant l'opération) ; les autres enregistrements (formulaires, connecteur Claude), et même la connexion, sont refusés avec le message « Restauration en cours » (503) au lieu d'être perdus (la déconnexion reste possible) ; les navigateurs gardent leur texte, se reconnectent ensuite et rechargent la fiche restaurée (ce qui avait été tapé entre-temps est gardé comme version « Modifications hors ligne »).

Une sauvegarde est écrite sous un nom provisoire (`maze-….tar.part`) puis renommée une fois complète : une sauvegarde qui échoue (disque plein…) ne laisse aucune archive incomplète parmi les sauvegardes, et les `.part` laissés par un arrêt brutal sont effacés à la sauvegarde suivante.

L'archive est vérifiée (contenu, intégrité de la base, version), l'état actuel est sauvegardé (`-avant-restauration`), puis la base est mise au schéma actuel et remplace la base en service avec les images. Comptes après une restauration :

- Les comptes sont ceux de l'archive, mais un compte présent des deux côtés (même identifiant interne et même nom de connexion) garde son mot de passe, son rôle et son état (actif ou désactivé) d'aujourd'hui : un mot de passe changé, un compte désactivé ou rétrogradé depuis la date de l'archive le restent. On peut donc restaurer la veille après une compromission sans rendre l'accès à l'intrus, que le compte compromis ait été désactivé ou supprimé (voir le dernier point).
- Toutes les sessions sont fermées (cookies, connecteur Claude, jetons Foundry) : chacun doit se reconnecter. Depuis l'interface, l'administrateur qui restaure reste connecté ; s'il n'existe pas dans l'archive, son compte y est ajouté (administrateur, mot de passe actuel), et s'il y figure sous le même nom de connexion, ce compte reprend son mot de passe et son nom affiché actuels.
- Les comptes créés après la date de l'archive disparaissent. Ceux de l'archive qui n'ont pas d'équivalent aujourd'hui (supprimés depuis, y compris quand leur identifiant interne a été repris par un compte créé depuis) reviennent **désactivés** : ils sont listés dans la réponse de l'API, dans le journal et dans le message affiché après la restauration ; un administrateur les réactive dans Administration > Comptes s'ils doivent revenir. Exception : pour annuler une restauration, en restaurant l'archive « avant-restauration » qu'elle vient de créer, les comptes qu'elle avait retirés (créés entre la date de l'archive restaurée et la restauration) reviennent tels qu'ils étaient, sans être désactivés. Maze retient pour cela la dernière restauration réussie dans `/data/backups/.derniere-restauration` ; la règle reste la même pour toute autre archive, y compris une copie de celle-là ou une archive « avant-restauration » plus ancienne.
- Connecteur Claude : identifiant et secret du client OAuth, accès activé ou suspendu, campagnes ouvertes (pour les campagnes présentes des deux côtés sous le même numéro et le même nom) et journal des actions de Claude sont ceux d'aujourd'hui ; une campagne de l'archive absente aujourd'hui revient fermée à Claude. Adresses connues des comptes (limitation des essais de mot de passe) : celles d'aujourd'hui, pour les comptes présents des deux côtés ; l'archive n'en ramène aucune. La clé secrète de l'instance (cookies) n'est pas dans les archives.
- En ligne de commande, personne n'est ajouté : la restauration est refusée si, après reprise des comptes actuels, aucun administrateur actif ne reste. Pour une reprise après sinistre sur une instance neuve (seul le compte `admin` du démarrage existe), ajouter `--keep-archive-accounts` : les comptes de l'archive sont repris tels quels, avec leurs mots de passe, ainsi que les réglages du connecteur Claude de l'archive. Sans base en service (volume neuf, base retirée), rien n'est désactivé.

      docker compose exec maze python -m maze.backup --restore /data/backups/maze-AAAAMMJJ-HHMMSS.tar.gz --keep-archive-accounts

- `--exact` (ligne de commande seulement, jamais depuis l'interface) : la base est remise exactement telle qu'archivée, sans aucune des règles ci-dessus : comptes, mots de passe, sessions ouvertes, jetons du connecteur Claude et de Foundry, réglages du connecteur sont ceux de l'archive (l'état actuel est quand même sauvegardé en `-avant-restauration`). C'est ce qu'utilise le retour automatique d'une mise à jour ratée, avec la sauvegarde `-maj` faite quelques minutes plus tôt par la même instance. Ne pas s'en servir avec une archive plus ancienne, ni après un incident de sécurité : les mots de passe changés, comptes désactivés, sessions et jetons révoqués depuis la date de l'archive reviendraient.

Si le remplacement des images échoue, la restauration est annulée : les images et la base d'avant sont remises en place (base reprise de l'archive « avant-restauration »). Si l'ancien dossier des images ne peut pas non plus reprendre sa place, le message le dit et donne son chemin exact (`/data/.media-remplace-…`) et la commande à faire, Maze arrêté. Taille maximale d'un import depuis l'interface : `MAZE_MAX_IMPORT_MB` (4096 par défaut).

Fichiers temporaires d'un arrêt brutal (import ou restauration tués en cours de route, mémoire épuisée, `docker stop` au-delà du délai) : retirés au démarrage suivant (`/data/.import-*`, `/data/tmp*.zip`, `/data/backups/.import-*.part`, `/data/.restauration-*` sauf restauration en cours en ligne de commande, images en cours d'écriture `media/<campagne>/.<nom>.tmp`). Restauration arrêtée net pendant le remplacement des images (base déjà restaurée, `media/` d'avant intact, absent ou déplacé) : au démarrage, si la base en service est bien celle de la sauvegarde restaurée (même empreinte que la base du dossier de travail `/data/.restauration-…`), ses images sont remises en place comme `media/` (le `media/` d'avant, s'il a des fichiers, est gardé dans un `/data/.media-remplace-…`) ; sinon, si `media/` est absent ou vide, le dossier de travail est gardé et signalé (journal, alerte « Restauration interrompue » d'Administration > Sauvegardes, `interrupted_restores`), sans rien remettre en place. Un dossier `/data/.media-remplace-…` n'est **jamais** supprimé automatiquement : ce sont les images d'avant une restauration interrompue, peut-être les seules si `media/` manque. Il est signalé dans le journal au démarrage et dans Administration > Sauvegardes (`replaced_media` de `GET /api/admin/backups/usage` ; alerte rouge si `media/` manque ou est vide, avec la commande à faire) ; après vérification (images de Maze toutes présentes), le supprimer à la main : `docker compose exec maze rm -r /data/.media-remplace-…`. Si `media/` manque ou est vide (Maze le recrée vide au démarrage), le remettre en place, les dossiers vides de `media/` d'abord retirés (rien n'est déplacé si `media/` contient un fichier) : `docker compose exec maze sh -c 'find /data/media -depth -type d -empty -delete; test ! -e /data/media && mv /data/.media-remplace-… /data/media'`. Le compose laisse 30 s à Maze pour s'arrêter (`stop_grace_period`).

### Copie hors de la machine

Les archives restent dans le volume `/data`, sur le même disque que la base : un disque mort ou une VM supprimée les emporte avec elle. Maze ne synchronise rien lui-même ; il faut copier régulièrement `/data/backups` ailleurs, depuis l'hôte.

Exemple avec rclone (SFTP vers un autre hôte, S3, Backblaze…), lancé dans un conteneur jetable qui monte le volume en lecture seule : aucun droit root, seulement le groupe docker, et rclone ne transfère que les archives nouvelles. Configuration une fois, en ops (ici un hôte SFTP nommé `distant`) :

    docker run --rm -it -v ~/.config/rclone:/config/rclone rclone/rclone:1.68.2 config

Nom du volume (dossier du dépôt + `_maze-data`, `maze_maze-data` pour /opt/maze) :

    docker volume ls --format '{{.Name}}' | grep maze-data

Copie (les `.part` en cours d'écriture sont exclus) :

    docker run --rm -v maze_maze-data:/data:ro -v ~/.config/rclone:/config/rclone:ro rclone/rclone:1.68.2 copy /data/backups distant:maze-backups --include 'maze-*.tar.gz'

Copie quotidienne après la sauvegarde de 3 h 15, par un second timer (ops ; remplacer `distant:maze-backups` par la destination configurée) :

    sudo tee /etc/systemd/system/maze-backup-copy.service >/dev/null <<'EOT'
    [Unit]
    Description=Maze - copie des sauvegardes hors de la machine
    Requires=docker.service
    After=docker.service maze-backup.service

    [Service]
    Type=oneshot
    User=ops
    Group=ops
    ExecStart=/usr/bin/docker run --rm -v maze_maze-data:/data:ro -v /home/ops/.config/rclone:/config/rclone:ro rclone/rclone:1.68.2 copy /data/backups distant:maze-backups --include 'maze-*.tar.gz'
    EOT
    sudo tee /etc/systemd/system/maze-backup-copy.timer >/dev/null <<'EOT'
    [Unit]
    Description=Maze - copie des sauvegardes à 3 h 45

    [Timer]
    OnCalendar=*-*-* 03:45
    Persistent=true

    [Install]
    WantedBy=timers.target
    EOT
    sudo systemctl daemon-reload
    sudo systemctl enable --now maze-backup-copy.timer
    sudo systemctl start maze-backup-copy.service
    journalctl -u maze-backup-copy -n 20 --no-pager

`rclone copy` ne supprime jamais rien à distance : la rotation de Maze n'y efface donc pas les archives déjà copiées. Pour borner la place à distance (ici 90 jours) :

    docker run --rm -v ~/.config/rclone:/config/rclone:ro rclone/rclone:1.68.2 delete distant:maze-backups --min-age 90d

Équivalent avec rsync vers un autre hôte par SSH : copier d'abord les archives hors du volume (`docker compose cp maze:/data/backups/. /srv/maze-backups/`), puis `rsync -a /srv/maze-backups/ sauvegarde@autre-hote:/srv/maze/`.

### Test de restauration périodique

Une sauvegarde n'est fiable que si elle se restaure. Une fois par mois (et après toute évolution des sauvegardes), restaurer la dernière archive copiée à distance dans une instance jetable, à côté de la production et sans la toucher :

    mkdir -p /tmp/essai-restauration
    docker run --rm -v /tmp/essai-restauration:/essai -v ~/.config/rclone:/config/rclone:ro rclone/rclone:1.68.2 copy distant:maze-backups /essai --max-age 2d
    ls -l /tmp/essai-restauration
    docker volume create maze-essai-restauration
    docker run -d --name maze-essai -e MAZE_PASSWORD=essai-restauration -v maze-essai-restauration:/data -p 127.0.0.1:8099:8000 maze:latest
    docker cp /tmp/essai-restauration/maze-AAAAMMJJ-031500.tar.gz maze-essai:/data/
    docker exec maze-essai python -m maze.backup --restore /data/maze-AAAAMMJJ-031500.tar.gz --keep-archive-accounts

`--keep-archive-accounts` : l'instance jetable est neuve (seul son compte `admin` de démarrage existe) ; sans cette option, les comptes de la production y seraient restaurés désactivés, ou la restauration refusée faute d'administrateur actif. La restauration doit se terminer par « État précédent sauvegardé dans … ». Vérifier ensuite le contenu et que chaque image citée par la base est bien là :

    docker exec -i maze-essai python - <<'EOF'
    import sqlite3
    from pathlib import Path
    c = sqlite3.connect("/data/maze.db")
    print("campagnes", c.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],
          "PNJ", c.execute("SELECT COUNT(*) FROM npcs").fetchone()[0],
          "fiches", c.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    files = [r[0] for sql in ("SELECT file FROM images", "SELECT thumb FROM images", "SELECT file FROM document_images",
                              "SELECT file FROM library_images", "SELECT file FROM maps", "SELECT preview FROM maps")
             for r in c.execute(sql) if r[0]]
    missing = [f for f in files if not Path("/data/media", f).is_file()]
    print(len(files), "fichiers d'images cités,", len(missing), "absents", missing[:5])
    EOF

Facultatif : ouvrir l'instance par un tunnel (`ssh -L 8099:127.0.0.1:8099 ops@machine-maze`, puis http://127.0.0.1:8099) et se connecter avec un compte de la production (ou `admin` / essai-restauration si la production a un compte `admin` de même identifiant). Puis tout effacer :

    docker rm -f maze-essai
    docker volume rm maze-essai-restauration
    rm -rf /tmp/essai-restauration

## Développement local (sans Docker)

    cd frontend && npm install && npm run watch        # terminal 1
    cd backend && pip install -r requirements.txt       # terminal 2
    MAZE_DATA_DIR=../data MAZE_STATIC_DIR=../frontend/dist uvicorn maze.app:app_factory --factory --reload --port 8000

Tests du serveur (dépendances de requirements.txt, dont websockets pour l'édition en temps réel ; tout fichier `backend/tests/test_*.py` est pris, aucune liste à tenir) et lint (ruff 0.16.8) :

    cd backend && python -m unittest discover -s tests
    cd backend && ruff check --isolated --select F,E9,B --line-length 120 maze tests

Tests de la logique pure de l'interface (`node --test`, Node 22, aucune dépendance : tout fichier `frontend/test/*.test.mjs` est pris ; `paths.test.mjs` reconstruit l'interface depuis une copie dans « Mes Documents/Maze é » : la construction marche avec une espace ou un accent dans le chemin, et sous Windows ; `foundry-module.test.mjs` teste la logique du module Foundry, `backend/maze/foundry_module/scripts/maze-sync.mjs`, sans Foundry) :

    cd frontend && npm test

Champs des fiches PNJ, factions et lieux : décrits une seule fois, dans `backend/maze/entries.py` (modèles de l'API, historique, commentaires, connecteur Claude, export Foundry, export / import en dérivent). Après une modification, recopier le registre pour l'interface : `cd backend && python -m maze.entries > ../frontend/src/entry-fields.generated.js` ; `tests.test_registry` signale ce qui manque (colonne, déclencheurs de la recherche et de la révision dans une nouvelle migration, fichier de l'interface).

Parcours navigateur (Playwright installé globalement, pas une dépendance du projet : `npm install -g playwright@1.56.0 && playwright install chromium`) : tous les scripts de `frontend/e2e/`, chacun sur une instance neuve lancée par le script (base vide, port 8790), interface construite au préalable :

    cd frontend && npm ci && npm run build && cd ..                   # npm install tant que package-lock.json manque
    NODE_PATH="$(npm root -g)" frontend/e2e/run-all.sh                 # ou : run-all.sh edition-fiches mise-a-jour

Variables de `run-all.sh` : `PYTHON` (interpréteur du serveur), `MAZE_STATIC_DIR` (interface, `frontend/dist` par défaut), `MAZE_E2E_PORT`, `MAZE_E2E_KEEP=1` (garder données et captures) ; il passe à chaque parcours le dossier de données de son instance (`MAZE_E2E_DATA_DIR`, utilisé par `finitions-interface.mjs`, qui sinon simule la réponse du serveur). Un parcours se lance aussi seul contre une instance existante : `node frontend/e2e/<parcours>.mjs http://127.0.0.1:8000` (base vide, ou `MAZE_E2E_PASSWORD` du compte `MAZE_E2E_USER` ; `mise-a-jour.mjs` demande en plus `MAZE_E2E_CONTROL_DIR` et `MAZE_E2E_STATUS_DIR`, voir l'en-tête du script).

Intégration continue : `.github/workflows/ci.yml` (GitHub Actions) rejoue tout cela (tests du serveur, de l'interface, parcours navigateur) à chaque envoi et demande de fusion, plus le contrôle de la numérotation des migrations et de la version du module Foundry (`foundry_module/module.json` à augmenter si le module change).

Accès à la base dans une route : fonction ordinaire décorée `@threaded` (`api.py`), qui s'exécute dans un fil réservé à SQLite, jamais dans la boucle d'événements (sinon une attente de verrou fige tout le serveur) ; `parse_body` pour lire le JSON, `with tx(request) as conn:` pour la transaction (lecture seule pour GET, `BEGIN IMMEDIATE` sinon). Une route qui doit rester asynchrone (envoi de fichiers) passe par `await in_tx(request, fonction)`. Base occupée au-delà de 15 s : réponse 503 « réessayez ».

Schéma de l'éditeur (`frontend/src/editor/schema.js`) : toute modification des types de blocs, des marques ou de leurs attributs exige d'augmenter la version dans `frontend/src/editor/schema-version.json` (et `SCHEMA_VERSION` dans `backend/maze/collab.py`) et d'y recopier la nouvelle empreinte, que donne `node build.mjs --schema-fingerprint` : sinon la construction de l'interface échoue. Les onglets ouverts sur l'ancienne version sont alors rechargés à la mise à jour.

## Structure

    backend/maze/
      app.py            assemblage ASGI, sécurité, médias, interface
      api.py            routes REST
      auth.py           comptes, mots de passe, droits sur les campagnes
      admin.py          API d'administration (comptes, campagnes, sauvegardes, mise à jour)
      documents.py      fiches rédigées (campagne, scénarios, sous-pages), mentions, images
      collab.py         édition en temps réel : relais WebSocket des modifications Yjs
      transfer.py       export / import d'une campagne seule (.zip)
      comments.py       commentaires des fiches (fils, réponses, résolution)
      search.py         recherche globale (index plein texte FTS5)
      versions.py       historique des versions des fiches rédigées
      places.py         lieux (arborescence, liens avec les PNJ et factions)
      templates.py      modèles de fiches enregistrés par les MJ
      schemas.py        validation des données (Pydantic)
      entries.py        registre des champs des PNJ, factions et lieux (source unique)
      db.py             SQLite, transactions, migrations versionnées
      migrations/       scripts SQL numérotés (001_initial.sql, ...)
      imaging.py        traitement des images
      mediafiles.py     extensions des fichiers de media/ (liste unique : /media, export, sauvegardes)
      limits.py         taille maximale des corps de requête (413)
      throttle.py       limitation des essais de mot de passe (adresse, délai par compte)
      demo.py, backup.py
    deploy/
      update.sh         mise à jour (pull, build, sauvegarde, redémarrage, contrôle, retour arrière)
      maze-updater.sh   service hôte déclenché depuis l'interface (systemd/) : demandes dans control/, état dans status/
    frontend/src/
      graph/            carte Cytoscape (rendu, style, éléments, finition de la disposition : declutter.js)
      editor/           éditeur des scénarios (ProseMirror, schéma, commandes, connexion temps réel, modèles fournis)
      components/       panneaux, dialogues, recherche, filtres
      components/scenarios/  onglet Scénarios (arbre, barre, panneau latéral, crochets des données, titre, versions, commentaires)
      components/maps/  carte interactive (carte, anneaux, zoom, fiche du lieu, liste des lieux, réglages)
      cast.js           distribution d'un scénario (castOf), comparée aux règles du serveur
      fields.js         description des fiches (affichage + édition), d'après entry-fields.generated.js (registre)
      session.js        saisies en cours, texte non fusionné gardé, autre version de Maze (avis : SessionNotices.jsx)
    frontend/schema-fingerprint.mjs   empreinte du schéma de l'éditeur, vérifiée par build.mjs
    frontend/e2e/       parcours navigateur (Playwright)
    frontend/test/      tests de la logique pure de l'interface (node --test)

## API

| Méthode | Route | Rôle |
|---|---|---|
| GET, POST | /api/campaigns | Lister, créer |
| GET, PATCH, DELETE | /api/campaigns/{id} | Lire, modifier, supprimer |
| GET | /api/campaigns/{id}/graph | PNJ, factions, appartenances, relations |
| PUT | /api/campaigns/{id}/positions | Positions sur la carte (lot) |
| POST | /api/campaigns/{id}/npcs, /factions, /relations | Créer |
| GET, PATCH, DELETE | /api/npcs/{id}, /api/factions/{id} | Fiche complète avec images ; `revision` dans la fiche, `base_revision` facultatif au PATCH (409 avec l'état actuel si la fiche a changé) |
| POST | /api/npcs/{id}/images, /api/factions/{id}/images | Envoi d'images (multipart, champ files) |
| POST, PATCH, DELETE | /api/memberships(/{id}) | Appartenances |
| PATCH, DELETE | /api/relations/{id}, /api/images/{id} | Modifier, supprimer |
| GET, POST | /api/campaigns/{id}/documents | Arbre des fiches rédigées, création |
| GET, PATCH, DELETE | /api/documents/{id} | Fiche rédigée (PATCH content remplace le texte et réinitialise l'édition en cours) |
| GET | /api/campaigns/{id}/export | Archive .zip d'une campagne |
| POST | /api/campaigns/import | Import d'une archive (corps brut), crée une nouvelle campagne |
| GET, POST | /api/documents/{id}/comments | Fils de commentaires d'une fiche, nouveau fil ou réponse |
| PATCH, DELETE | /api/comments/{id} | Modifier, résoudre / rouvrir, supprimer |
| GET | /api/search?q=…&campaign=<id>\|all | Recherche globale |
| GET | /api/campaigns/{id}/documents/mentions | Fiches rédigées et fiches citées dans chacune |
| GET, POST | /api/campaigns/{id}/places | Lieux de la campagne et leurs liens, création |
| PUT | /api/campaigns/{id}/places/order | Ordre des lieux d'un même niveau |
| GET, PATCH, DELETE | /api/places/{id} | Fiche d'un lieu (sous-lieux, présents, mentions), modifier (`base_revision` facultatif comme les PNJ), supprimer |
| POST | /api/places/{id}/images, /api/places/{id}/links | Images, PNJ ou faction présent (avec rôle) |
| PATCH, DELETE | /api/place-links/{id} | Rôle, retrait d'un PNJ ou d'une faction |
| POST | /api/documents/{id}/duplicate | Copie d'une fiche et de ses sous-pages |
| GET, POST | /api/documents/{id}/versions | Historique d'une fiche, nouvelle version nommée |
| GET, PATCH, DELETE | /api/documents/{id}/versions/{vid} | Contenu d'une version, renommer, supprimer |
| POST | /api/documents/{id}/versions/{vid}/restore | Restaurer une version (l'état actuel est gardé) |
| GET, POST | /api/templates | Modèles de fiches des MJ |
| PATCH, DELETE | /api/templates/{id} | Renommer, supprimer (auteur ou administrateur) |
| WebSocket | /api/documents/{id}/live?client=…&v=… | Édition en temps réel (protocole décrit dans backend/maze/collab.py ; `v` : version du schéma de l'éditeur, autre version refusée, code 4426) |

En-tête facultatif `X-Maze-Version` (version du schéma de l'éditeur, envoyée par l'interface) : une écriture (POST, PUT, PATCH, DELETE) qui porte une autre version reçoit 409 (`"code": "version"`) ; sans l'en-tête (connecteur Claude, scripts), aucune vérification.

## Pistes pour la suite

- Plan illustré : une image (carte du monde, plan de ville) avec des épingles vers les fiches lieux.
- Notes de session rattachées aux scénarios joués ; comparaison visuelle entre deux versions d'une fiche.
- Accès joueurs : rôle joueur en lecture seule, sans les secrets ni les relations secrètes (déjà distingués dans le modèle).
