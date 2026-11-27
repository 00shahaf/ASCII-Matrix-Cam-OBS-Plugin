#!/bin/sh
# Adds ASCII Cam to your app menu and puts a double-clickable icon on your desktop.
# Run it again after moving this folder. Remove with: ./install-linux.sh --uninstall
set -e
APP_DIR=$(cd "$(dirname "$0")" && pwd)
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
DESKTOP=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")

if [ "$1" = "--uninstall" ]; then
    rm -f "$APPS/ascii-cam.desktop" "$ICONS/ascii-cam.svg" "$DESKTOP/ascii-cam.desktop"
    echo "Removed ASCII Cam shortcuts."
    exit 0
fi

python3 -c "import PySide6, numpy" 2>/dev/null || {
    echo "PySide6 and numpy are missing. Install them first:"
    echo "  pip install -r \"$APP_DIR/requirements.txt\""
    exit 1
}

mkdir -p "$APPS" "$ICONS"
cp "$APP_DIR/icon.svg" "$ICONS/ascii-cam.svg"
cat > "$APPS/ascii-cam.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ASCII Cam
Comment=Your webcam as live ASCII art or digital rain
Exec=python3 "$APP_DIR/ascii_cam.py"
Icon=ascii-cam
Terminal=false
Categories=AudioVideo;Video;
StartupWMClass=ascii-cam
EOF
chmod +x "$APPS/ascii-cam.desktop"

if [ -d "$DESKTOP" ]; then
    cp "$APPS/ascii-cam.desktop" "$DESKTOP/ascii-cam.desktop"
    chmod +x "$DESKTOP/ascii-cam.desktop"
    # GNOME needs the launcher marked as trusted; harmless elsewhere.
    gio set "$DESKTOP/ascii-cam.desktop" metadata::trusted true 2>/dev/null || true
fi
update-desktop-database "$APPS" 2>/dev/null || true
echo "Installed. Double-click \"ASCII Cam\" on your desktop, or find it in your app menu."
