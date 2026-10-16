# Installation de Maze sur un Debian neuf

Cas traité : Maze tourne sur une machine Debian dédiée, Caddy reste sur le reverse proxy existant (une autre machine du réseau privé) et lui transmet le trafic par le réseau privé.

À relever avant de commencer :
- IP_MAZE : IP privée de la nouvelle machine (ip -br a ; par exemple 10.0.0.10)
- IP_CADDY : IP privée du reverse proxy, vue depuis la machine Maze (par exemple 10.0.0.20)
- NOM_MAZE : nom public de Maze (maze.example.org dans les exemples)
- l'adresse du dépôt à cloner : git@github-maze:ranglier/Maze.git ci-dessous (dépôt https://github.com/ranglier/Maze), ou celle d'un fork

## 1. Système de base (en root)

    apt update && apt full-upgrade -y
    apt install -y sudo git curl ca-certificates unattended-upgrades
    timedatectl set-timezone Europe/Paris

Utilisateur d'administration avec la même clé SSH que root :

    adduser --gecos "" ops
    usermod -aG sudo ops
    install -d -m 700 -o ops -g ops /home/ops/.ssh
    install -m 600 -o ops -g ops /root/.ssh/authorized_keys /home/ops/.ssh/authorized_keys

Tester la connexion ssh ops@IP_MAZE dans une autre fenêtre, puis seulement ensuite :

    tee /etc/ssh/sshd_config.d/10-durcissement.conf >/dev/null <<'EOF'
    PermitRootLogin no
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    EOF
    sshd -t && systemctl reload ssh

## 2. Docker (dépôt officiel), en ops

    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt update
    sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

Rotation des journaux des conteneurs :

    sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
    { "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
    EOF
    sudo systemctl restart docker

Accès à Docker sans sudo, puis se déconnecter et se reconnecter :

    sudo usermod -aG docker ops

Le groupe docker équivaut à root : ops, et le service maze-updater qui tourne sous ce compte, peuvent tout faire sur la machine. C'est pourquoi le conteneur n'a pas accès à Docker et ne peut écrire que sa demande de mise à jour (docs/fr/guide.md, « Mise à jour depuis l'interface »). Pour aller plus loin, Docker peut tourner sans root (mode rootless : https://docs.docker.com/engine/security/rootless/) ; facultatif, non décrit ici.

Contrôle après reconnexion :

    docker run --rm hello-world
    docker compose version

## 3. Clé de déploiement GitHub (lecture seule)

    ssh-keygen -t ed25519 -f ~/.ssh/maze_deploy -N "" -C "deploy maze@$(hostname)"
    cat ~/.ssh/maze_deploy.pub

Ajouter la clé dans GitHub : dépôt Maze > Settings > Deploy keys > Add deploy key, sans "Allow write access".

    cat >> ~/.ssh/config <<'EOF'
    Host github-maze
        HostName github.com
        User git
        IdentityFile ~/.ssh/maze_deploy
        IdentitiesOnly yes
    EOF
    chmod 600 ~/.ssh/config
    ssh -T github-maze

La dernière commande doit répondre "successfully authenticated" (après acceptation de l'empreinte de github.com).

## 4. Code et configuration

    sudo mkdir -p /opt/maze && sudo chown ops: /opt/maze
    git clone git@github-maze:ranglier/Maze.git /opt/maze
    cd /opt/maze
    cp .env.example .env
    sed -i "s/^MAZE_BIND=.*/MAZE_BIND=IP_MAZE/" .env
    sed -i "s/^FORWARDED_ALLOW_IPS=.*/FORWARDED_ALLOW_IPS=IP_CADDY/" .env

(remplacer IP_MAZE et IP_CADDY par les IP relevées au début). FORWARDED_ALLOW_IPS : seul le reverse proxy est cru quand il transmet l'adresse du client (X-Forwarded-For) et le protocole HTTPS (X-Forwarded-Proto). Laissée vide, elle vaut les réseaux privés et locaux, qui contiennent Caddy : cela fonctionne aussi, mais un appareil du réseau local qui joindrait directement Maze pourrait choisir son adresse apparente ; l'IP exacte est recommandée. Un proxy non approuvé est signalé dans Administration > Mise à jour. Après une modification de .env : docker compose up -d.

Mot de passe de l'application, saisie masquée, à lancer seule :

    read -rs -p "Mot de passe Maze : " MAZE_PW && echo " - saisi (${#MAZE_PW} caractères)"

Puis :

    mkdir -p secrets
    printf '%s' "$MAZE_PW" > secrets/maze_password.txt
    unset MAZE_PW
    chmod 600 secrets/maze_password.txt
    sudo chown 1000:1000 secrets/maze_password.txt

## 5. Premier lancement

    docker compose up -d --build
    docker compose ps
    curl -s http://IP_MAZE:8000/api/health

Réponse attendue : {"status":"ok"}. Campagne de démonstration (facultatif) :

    docker compose exec maze python -m maze.demo

## 6. Caddy (sur le reverse proxy)

Vérifier que le proxy joint Maze :

    curl -s http://IP_MAZE:8000/api/health

Ajouter au Caddyfile le bloc de deploy/Caddyfile.maze, en remplaçant maze.example.org par NOM_MAZE et l'adresse de reverse_proxy par :

    reverse_proxy IP_MAZE:8000

Le bloc fourni limite la taille des requêtes de façon cohérente avec Maze : 4 Gio pour les imports d'archives (sauvegardes, campagnes), 410 Mio ailleurs. Un ancien bloc avec request_body max_size 60MB pour tout le site refuse les imports de sauvegardes plus grosses : reprendre les deux blocs request_body du modèle.

Puis :

    sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
    sudo systemctl reload caddy

## 7. Sauvegarde quotidienne (sur la machine Maze)

Timer systemd à 3 h 15 (14 sauvegardes quotidiennes conservées dans le volume, /data/backups, réglable par MAZE_BACKUP_KEEP dans .env ; les archives manuelles, d'avant mise à jour, d'avant restauration et importées ont leur propre rotation, voir docs/fr/guide.md). Debian minimale : pas de cron, et pas besoin de courrier, la sortie va dans le journal.

    cd /opt/maze
    sudo cp deploy/systemd/maze-backup.service deploy/systemd/maze-backup.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now maze-backup.timer
    sudo systemctl start maze-backup.service
    systemctl list-timers maze-backup.timer --no-pager
    journalctl -u maze-backup -n 20 --no-pager

Attendu : le nom de l'archive créée dans le journal, et la prochaine exécution du timer.

Ces archives restent sur le disque de la machine : ajouter une copie hors machine et un test de restauration périodique (docs/fr/guide.md, sections « Copie hors de la machine » et « Test de restauration périodique »).

## 8. Mise à jour depuis l'interface (facultatif)

Installer le service maze-updater : voir docs/fr/guide.md, section « Mise à jour depuis l'interface ». Le conteneur dépose sa demande dans /opt/maze/control (seul dossier où il écrit) ; le service publie l'état dans /opt/maze/status, créé par git et monté en lecture seule dans le conteneur.

## Mises à jour

    cd /opt/maze && ./deploy/update.sh

En cas d'échec, l'ancienne version reste ou revient en service automatiquement (docs/fr/guide.md, « Mises à jour »).

## Remarque sur le pare-feu

Les ports publiés par Docker ne passent pas par les règles INPUT d'ufw : la protection repose sur MAZE_BIND, qui limite l'écoute à l'interface privée. Ne jamais mettre 0.0.0.0. Si ufw est activé, autoriser SSH avant : sudo ufw allow 22/tcp (adapter le port).
