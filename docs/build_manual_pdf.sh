#!/bin/sh
# Build docs/manual.md into a standalone PDF: pandoc makes one self-contained HTML
# file (docs/manual-pdf.css), and headless Chrome prints it. No LaTeX needed.
# Usage, from the repo root:  sh docs/build_manual_pdf.sh [out.pdf]
# The PDF is a snapshot: the contents page names the build it came from.
set -e
OUT="${1:-spec-echem-manual.pdf}"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
BUILD=$(python3 -c "import spec_echem; print(spec_echem.build_id())" 2>/dev/null || echo "unknown build")
TMP=$(mktemp -d)
pandoc docs/manual.md -s --toc --toc-depth=2 --embed-resources --resource-path=docs \
    -c docs/manual-pdf.css --metadata pagetitle="spec-echem user manual" \
    --metadata toc-title="spec-echem user manual: contents (built $(date +%Y-%m-%d), $BUILD)" \
    -o "$TMP/manual.html"
"$CHROME" --headless --disable-gpu --no-pdf-header-footer \
    --print-to-pdf="$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")" \
    "file://$TMP/manual.html" >/dev/null 2>&1
rm -rf "$TMP"
echo "wrote $OUT"
