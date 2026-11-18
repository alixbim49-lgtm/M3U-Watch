#!/usr/bin/env bash
# Installation de M3U Watch sur Lubuntu / Ubuntu / Debian.
# A lancer SANS sudo (le script demande le mot de passe quand il en a besoin) :  bash install.sh
set -e
if [ "$(id -u)" -eq 0 ]; then
  echo "Lance ce script avec ton utilisateur normal, sans sudo :  bash install.sh"
  exit 1
fi
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="$(id -un)"

echo "==> 1/3  Paquets : Python 3 et Tk (interface graphique)"
sudo apt-get update
sudo apt-get install -y python3 python3-tk

echo "==> 2/3  Service d'arrière-plan (envois automatiques, même sans session ouverte)"
sudo tee /etc/systemd/system/m3u-watch.service >/dev/null <<UNIT
[Unit]
Description=M3U Watch (surveillance de playlists IPTV)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 -u $DIR/m3u_monitor.py --daemon
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now m3u-watch.service

echo "==> 3/3  Lanceur « M3U Watch » (menu des applications et bureau)"
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/m3u-watch.desktop" <<ENTRY
[Desktop Entry]
Type=Application
Name=M3U Watch
Comment=Surveillance de playlists IPTV
Exec=/usr/bin/python3 $DIR/m3u_gui.py
Path=$DIR
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;
ENTRY
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESK" ]; then
  cp "$APPS/m3u-watch.desktop" "$DESK/"
  chmod +x "$DESK/m3u-watch.desktop"
fi

echo
echo "Terminé. Ouvre « M3U Watch » depuis le menu ou l'icône du bureau."
echo "État du service :  systemctl status m3u-watch"
