#!/bin/sh
# Linux build. Uses the system libobs (Debian/Ubuntu: libobs-dev libsimde-dev) or, if that
# isn't installed, the headers bundled in the Flatpak OBS. Then installs for the current user.
#   ./build.sh               build and install
#   ./build.sh --no-install  build only (build/ascii-cam.so)
set -e
cd "$(dirname "$0")"
FLATPAK=/var/lib/flatpak/app/com.obsproject.Studio/current/active/files

if pkg-config --exists libobs 2>/dev/null; then
    CFLAGS_OBS=$(pkg-config --cflags libobs)
    LIBS_OBS=$(pkg-config --libs libobs)
elif [ -f "$FLATPAK/include/obs/obs-module.h" ]; then
    CFLAGS_OBS="-I$FLATPAK/include/obs -I$FLATPAK/include"
    LIBS_OBS="-L$FLATPAK/lib -l:libobs.so.30"
else
    echo "No libobs found. Install libobs-dev (and libsimde-dev), or the Flatpak OBS."
    exit 1
fi

[ -f data/atlas.png ] || python3 make_atlas.py
mkdir -p build
# shellcheck disable=SC2086
gcc -O2 -fPIC -shared -std=gnu11 -Wall -Wextra -Wno-missing-field-initializers \
    $CFLAGS_OBS -o build/ascii-cam.so src/ascii-cam-filter.c $LIBS_OBS -lm
echo "built build/ascii-cam.so"

[ "$1" = "--no-install" ] && exit 0
if [ -d "$HOME/.var/app/com.obsproject.Studio" ]; then
    DEST="$HOME/.var/app/com.obsproject.Studio/config/obs-studio/plugins/ascii-cam"
else
    DEST="${XDG_CONFIG_HOME:-$HOME/.config}/obs-studio/plugins/ascii-cam"
fi
mkdir -p "$DEST/bin/64bit" "$DEST/data"
cp build/ascii-cam.so "$DEST/bin/64bit/"
cp data/ascii-cam.effect data/atlas.png "$DEST/data/"
echo "installed to $DEST (restart OBS)"
