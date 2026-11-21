#!/bin/bash
# make_app.sh - puts an Unsay app (with its icon) on your Desktop (macOS). Run once:  bash make_app.sh
# Clicking it starts Unsay with NO Terminal window on screen. It still runs through Terminal
# (hidden), so the mic / camera / keyboard permissions you already gave keep working.
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
APP="$HOME/Desktop/Unsay.app"
TMP=$(mktemp -d)

echo "1/3 building the icon..."
mkdir "$TMP/Unsay.iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s assets/icon.png --out "$TMP/Unsay.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) assets/icon.png --out "$TMP/Unsay.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$TMP/Unsay.iconset" -o "$TMP/Unsay.icns"

echo "2/3 building Unsay.app..."
rm -rf "$APP"
osacompile -o "$APP" <<EOF
set d to "$DIR"
set cmd to "cd " & quoted form of d & " && ./Unsay.command; exit"
set wasRunning to application "Terminal" is running
tell application "Terminal"
	if wasRunning then
		do script cmd
	else
		do script cmd in window 1
	end if
	delay 0.4
	try
		set visible of front window to false
	end try
end tell
EOF
cp "$TMP/Unsay.icns" "$APP/Contents/Resources/applet.icns"
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
touch "$APP"

echo "3/3 done: Unsay is on your Desktop. Drag it to the Dock if you like."
echo "First click: macOS asks 'Unsay wants to control Terminal' -> press OK (once)."
