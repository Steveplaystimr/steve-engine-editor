#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
GLIBC_VERSION=$(getconf GNU_LIBC_VERSION | awk '{print $2}')

BUILD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/steve-engine-deb.XXXXXX")
trap 'rm -rf -- "$BUILD_DIR"' EXIT HUP INT TERM

SOURCE_DIR="$BUILD_DIR/source"
VENV_DIR="$BUILD_DIR/venv"
PACKAGE_ROOT="$BUILD_DIR/package"
mkdir -p "$SOURCE_DIR"

cp -a \
    "$ROOT/main.py" \
    "$ROOT/game_player.py" \
    "$ROOT/engine_editor" \
    "$ROOT/assets" \
    "$ROOT/sounds" \
    "$ROOT/mods" \
    "$ROOT/sse_modding_tools.py" \
    "$ROOT/requirements.txt" \
    "$ROOT/build_native.py" \
    "$ROOT/CMakeLists.txt" \
    "$ROOT/engine-editor.spec" \
    "$ROOT/game-player.spec" \
    "$ROOT/icon.ico" \
    "$ROOT/LICENSE.txt" \
    "$ROOT/README.txt" \
    "$ROOT/MODDING.md" \
    "$SOURCE_DIR/"
cp -a "$ROOT/debian" "$SOURCE_DIR/"

cd "$SOURCE_DIR"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r requirements.txt
"$VENV_DIR/bin/python" build_native.py
"$VENV_DIR/bin/python" -m PyInstaller --noconfirm --clean engine-editor.spec
"$VENV_DIR/bin/python" -m PyInstaller --noconfirm --clean game-player.spec

APP_DIR="$SOURCE_DIR/dist/engine-editor"
PLAYER_DIR="$SOURCE_DIR/dist/steve-game-player"
if [ ! -x "$APP_DIR/engine-editor" ]; then
    echo "PyInstaller did not produce the Linux editor executable." >&2
    exit 1
fi
if [ ! -x "$PLAYER_DIR/steve-game-player" ]; then
    echo "PyInstaller did not produce Steve Game Player." >&2
    exit 1
fi

ARCH=$(dpkg --print-architecture)
PACKAGE_NAME="steve-engine-editor_1.2.2.1_${ARCH}.deb"
PACKAGE_PATH="$ROOT/dist/$PACKAGE_NAME"
mkdir -p \
    "$PACKAGE_ROOT/DEBIAN" \
    "$PACKAGE_ROOT/usr/bin" \
    "$PACKAGE_ROOT/usr/lib/steve-engine-editor" \
    "$PACKAGE_ROOT/usr/lib/steve-engine-editor/player" \
    "$PACKAGE_ROOT/usr/share/applications" \
    "$PACKAGE_ROOT/usr/share/doc/steve-engine-editor"
cp -a "$APP_DIR/." "$PACKAGE_ROOT/usr/lib/steve-engine-editor/"
cp -a "$PLAYER_DIR/." "$PACKAGE_ROOT/usr/lib/steve-engine-editor/player/"
cp -a "$SOURCE_DIR/mods" "$PACKAGE_ROOT/usr/lib/steve-engine-editor/mods"
cp "$SOURCE_DIR/LICENSE.txt" \
    "$PACKAGE_ROOT/usr/share/doc/steve-engine-editor/copyright"
cp "$SOURCE_DIR/README.txt" "$SOURCE_DIR/MODDING.md" "$SOURCE_DIR/debian/README" \
    "$PACKAGE_ROOT/usr/share/doc/steve-engine-editor/"
cp "$SOURCE_DIR/debian/steve-engine-editor.desktop" \
    "$PACKAGE_ROOT/usr/share/applications/steve-engine-editor.desktop"
cp "$SOURCE_DIR/debian/steve-game-player.desktop" \
    "$PACKAGE_ROOT/usr/share/applications/steve-game-player.desktop"
cp "$SOURCE_DIR/debian/steve-engine-editor" "$PACKAGE_ROOT/usr/bin/steve-engine-editor"
cp "$SOURCE_DIR/debian/steve-game-player" "$PACKAGE_ROOT/usr/bin/steve-game-player"
chmod 755 "$PACKAGE_ROOT/usr/bin/steve-engine-editor"
chmod 755 "$PACKAGE_ROOT/usr/bin/steve-game-player"
sed "s/@ARCH@/$ARCH/; s/@GLIBC@/$GLIBC_VERSION/" "$SOURCE_DIR/debian/control.in" \
    > "$PACKAGE_ROOT/DEBIAN/control"
mkdir -p "$ROOT/dist"
dpkg-deb --build --root-owner-group "$PACKAGE_ROOT" "$PACKAGE_PATH"
printf 'Created Debian package: %s\n' "$PACKAGE_PATH"
