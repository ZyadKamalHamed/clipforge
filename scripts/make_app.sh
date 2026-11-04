#!/bin/bash
set -euo pipefail
# Builds ClipForge.app, which opens ClipForge from Finder, the Dock or Spotlight (SPEC 20). It goes in
# ${APP_DIR:-$HOME/Applications}, built in a temp folder first so a half-built app is never left there.

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
apps="${APP_DIR:-$HOME/Applications}"
app="$apps/ClipForge.app"
clipforge="$repo/.venv/bin/clipforge"
id="local.clipforge.launcher"

if [[ ! -x "$clipforge" ]]; then
  echo "ClipForge isn't installed in $repo yet. Run scripts/install.sh first." >&2
  exit 1
fi
if [[ "$repo" == *[\"\$\`\\]* ]]; then
  echo "Move the ClipForge folder somewhere whose path has no quote, \$, \` or \\ in it, then try again." >&2
  exit 1
fi
if [[ -e "$app" ]] && [[ "$(/usr/bin/plutil -extract CFBundleIdentifier raw -o - "$app/Contents/Info.plist" 2>/dev/null)" != "$id" ]]; then
  echo "$app is another app, so it was left alone. Move it, then try again." >&2
  exit 1
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/clipforge-app.XXXXXX")"
trap 'rm -rf "$work"' EXIT
contents="$work/ClipForge.app/Contents"
mkdir -p "$contents/MacOS"

cat > "$contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>ClipForge</string>
  <key>CFBundleDisplayName</key>
  <string>ClipForge</string>
  <key>CFBundleIdentifier</key>
  <string>$id</string>
  <key>CFBundleExecutable</key>
  <string>ClipForge</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
</dict>
</plist>
PLIST

# The app has no Terminal to show a problem in, so one that stops ClipForge opening is shown in an alert.
cat > "$contents/MacOS/ClipForge" <<EXE
#!/bin/bash
if out=\$("$clipforge" open 2>&1); then
  exit 0
fi
message="\$(printf '%s\n' "\$out" | tail -n 6)"
/usr/bin/osascript -e 'on run argv' -e 'display alert "ClipForge" message (item 1 of argv) as critical' \\
  -e 'end run' "\${message:-ClipForge stopped without saying why.}" >/dev/null 2>&1 || true
exit 1
EXE
chmod 755 "$contents/MacOS/ClipForge"

# The notifier helper (SPEC 18): a small app inside this one that shows ClipForge's notifications and opens
# the page one is about when it is clicked. Without the Swift compiler, or if it won't build, ClipForge
# falls back to osascript's notifications, which can't open anything.
swiftc="${SWIFTC:-/usr/bin/swiftc}"
codesign="${CODESIGN:-/usr/bin/codesign}"
helper="$contents/Helpers/ClipForge Notifier.app"
if xcode-select -p >/dev/null 2>&1 && [[ -x "$swiftc" ]]; then
  mkdir -p "$helper/Contents/MacOS"
  cat > "$helper/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>ClipForge</string>
  <key>CFBundleDisplayName</key>
  <string>ClipForge</string>
  <key>CFBundleIdentifier</key>
  <string>$id.notifier</string>
  <key>CFBundleExecutable</key>
  <string>ClipForge Notifier</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
</dict>
</plist>
PLIST
  if "$swiftc" -O -o "$helper/Contents/MacOS/ClipForge Notifier" "$repo/scripts/notifier/ClipForgeNotifier.swift" \
    && "$codesign" --force --sign - "$helper"; then
    echo "Built the notifier, so clicking a notification opens its page."
  else
    rm -rf "$contents/Helpers"
    echo "The notifier couldn't be built, so notifications will come without opening ClipForge when clicked."
  fi
else
  echo "No Swift compiler, so notifications will come without opening ClipForge when clicked." \
    "To have them open it, run: xcode-select --install, then scripts/make_app.sh again."
fi

mkdir -p "$apps"
if [[ -e "$app" ]]; then
  rm -rf "$app.old"
  mv "$app" "$app.old"
fi
mv "$work/ClipForge.app" "$app"
rm -rf "$app.old"
echo "Built $app"
