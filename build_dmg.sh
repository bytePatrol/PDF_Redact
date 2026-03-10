#!/bin/bash
# Builds a distributable DMG for PDF Redactor.
set -euo pipefail

cd "$(dirname "$0")"

# ── Config ─────────────────────────────────────────────────────────────────────
APP_BUNDLE="PDF Redactor.app"
VOLUME_NAME="PDF Redactor"
DMG_FINAL="PDF Redactor.dmg"
DMG_TEMP="._build_temp.dmg"
STAGING="._dmg_staging"
ICNS="$APP_BUNDLE/Contents/Resources/AppIcon.icns"
LOGO="logo.png"
BG_PNG="._dmg_background.png"

# Window dimensions & icon positions
WIN_W=660;  WIN_H=420
APP_X=170;  APP_Y=185    # app icon centre (x, y)
APPS_X=490; APPS_Y=185   # Applications symlink centre
ICON_SIZE=120

echo "▶  Building DMG for $APP_BUNDLE"

# ── 1. Draw background ──────────────────────────────────────────────────────────
echo "   Creating background image…"
python3 - "$BG_PNG" "$WIN_W" "$WIN_H" "$APP_X" "$APP_Y" "$APPS_X" "$APPS_Y" << 'PYEOF'
import sys, math
from PIL import Image, ImageDraw

out, W, H = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
ax, ay = int(sys.argv[4]), int(sys.argv[5])   # app icon
fx, fy = int(sys.argv[6]), int(sys.argv[7])   # folder icon

S = 2   # render at 2× then downsample for smooth edges
iw, ih = W * S, H * S
img = Image.new("RGB", (iw, ih), (255, 255, 255))
draw = ImageDraw.Draw(img)

# ── Arrow (cubic bezier from app icon right-edge to folder left-edge) ──────────
pad   = 38 * S
sx    = (ax + ICON_SIZE // 2 + pad) if False else ((ax + 70) * S)
sy    = ay * S
ex    = (fx - 70) * S
ey    = fy * S

# control points for a gentle S-curve like the Figma example
c1x, c1y = sx + 35 * S, sy - 22 * S
c2x, c2y = ex - 35 * S, ey + 22 * S

def bez(t, p0, p1, p2, p3):
    m = 1 - t
    return (m**3*p0[0] + 3*m**2*t*p1[0] + 3*m*t**2*p2[0] + t**3*p3[0],
            m**3*p0[1] + 3*m**2*t*p1[1] + 3*m*t**2*p2[1] + t**3*p3[1])

pts = [bez(i / 300, (sx, sy), (c1x, c1y), (c2x, c2y), (ex, ey))
       for i in range(301)]

lw = 5 * S
for i in range(len(pts) - 1):
    draw.line([pts[i], pts[i + 1]], fill=(18, 18, 18), width=lw)

# ── Arrowhead ──────────────────────────────────────────────────────────────────
tip   = pts[-1]
near  = pts[-12]
angle = math.atan2(tip[1] - near[1], tip[0] - near[0])
al, aw = 20 * S, 11 * S
left  = (tip[0] - al * math.cos(angle) + aw * math.sin(angle),
         tip[1] - al * math.sin(angle) - aw * math.cos(angle))
right = (tip[0] - al * math.cos(angle) - aw * math.sin(angle),
         tip[1] - al * math.sin(angle) + aw * math.cos(angle))
draw.polygon([tip, left, right], fill=(18, 18, 18))

img = img.resize((W, H), Image.LANCZOS)
img.save(out, "PNG")
print(f"      background → {out}  ({W}×{H})")
PYEOF

# ── 2. Stage files ─────────────────────────────────────────────────────────────
echo "   Staging files…"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -r "$APP_BUNDLE" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

# ── 3. Create writable HFS+ DMG ────────────────────────────────────────────────
echo "   Creating writable disk image…"
rm -f "$DMG_TEMP"
hdiutil create \
    -volname "$VOLUME_NAME" \
    -srcfolder "$STAGING" \
    -ov -format UDRW \
    -fs HFS+ \
    -size 80m \
    "$DMG_TEMP" > /dev/null

# ── 4. Mount ───────────────────────────────────────────────────────────────────
echo "   Mounting…"
MOUNT_OUT=$(hdiutil attach -readwrite -noverify -noautoopen "$DMG_TEMP" 2>&1)
MOUNT_PT=$(echo "$MOUNT_OUT" | grep -oE '/Volumes/[^\n]+' | tail -1 | xargs)
echo "      mounted at: $MOUNT_PT"

# ── 5. Background ──────────────────────────────────────────────────────────────
echo "   Copying background…"
mkdir -p "$MOUNT_PT/.background"
cp "$BG_PNG" "$MOUNT_PT/.background/background.png"

# ── 6. Configure Finder window via AppleScript ─────────────────────────────────
echo "   Configuring Finder window…"
VOL="$VOLUME_NAME"
WL=$((200))
WT=$((150))
WR=$((200 + WIN_W))
WB=$((150 + WIN_H))
osascript << ASEOF
tell application "Finder"
    -- open once and configure in a single pass (no close/reopen)
    set tgt to disk "$VOL"
    open tgt
    delay 1

    set win to container window of tgt
    set toolbar visible of win to false
    set statusbar visible of win to false
    set the bounds of win to {$WL, $WT, $WR, $WB}

    set opts to icon view options of win
    set arrangement of opts to not arranged
    set icon size of opts to $ICON_SIZE
    set text size of opts to 13
    set background picture of opts to ¬
        file ".background:background.png" of tgt

    set position of item "PDF Redactor.app" of win to {$APP_X, $APP_Y}
    set position of item "Applications"     of win to {$APPS_X, $APPS_Y}

    update tgt without registering applications
    delay 2
    close win
end tell
ASEOF

# Flush DS_Store
sync
sleep 1

# ── 7. Volume icon (set AFTER Finder is done to prevent it being stripped) ─────
echo "   Setting volume icon…"
cp "$ICNS" "$MOUNT_PT/.VolumeIcon.icns"
/usr/bin/SetFile -a C "$MOUNT_PT"               # set kHasCustomIcon on volume
/usr/bin/SetFile -a V "$MOUNT_PT/.VolumeIcon.icns"  # mark file invisible
echo "      done ($(ls -la "$MOUNT_PT/.VolumeIcon.icns" 2>/dev/null | awk '{print $5}') bytes)"

# ── 8. Unmount ─────────────────────────────────────────────────────────────────
echo "   Unmounting…"
sync
hdiutil detach "$MOUNT_PT" -quiet

# ── 9. Compress to final DMG ───────────────────────────────────────────────────
echo "   Compressing…"
rm -f "$DMG_FINAL"
hdiutil convert "$DMG_TEMP" -format UDZO -imagekey zlib-level=9 -o "$DMG_FINAL" > /dev/null

# ── 10. Set DMG file icon ──────────────────────────────────────────────────────
echo "   Setting DMG file icon…"
python3 - "$DMG_FINAL" "$ICNS" << 'ICONEOF'
import sys, os
from AppKit import NSWorkspace, NSImage
dmg  = os.path.abspath(sys.argv[1])
icns = os.path.abspath(sys.argv[2])
img  = NSImage.alloc().initWithContentsOfFile_(icns)
ok   = NSWorkspace.sharedWorkspace().setIcon_forFile_options_(img, dmg, 0)
print("      DMG file icon set:", ok)
ICONEOF

# ── Cleanup ────────────────────────────────────────────────────────────────────
rm -f "$DMG_TEMP" "$BG_PNG"
rm -rf "$STAGING"

echo ""
echo "✓  Done: $DMG_FINAL"
ls -lh "$DMG_FINAL"
