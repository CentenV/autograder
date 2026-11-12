#!/usr/bin/env bash
# Package one project as a Gradescope autograder zip.
#   tools/make_zip.sh p4_routing          -> dist/p4_routing.zip
#   tools/make_zip.sh p1_webserver 560    -> dist/p1_webserver-560.zip (variant-specific tests)
#
# Zip layout (Gradescope needs run_autograder and setup.sh at the top):
#   run_autograder setup.sh requirements.txt pytest.ini   (from src/autograder/gradescope/)
#   conftest.py PROJECT [VARIANT]
#   autograder/                    (the framework's *.py, without gradescope/)
#   projects/$PROJECT/
set -euo pipefail

PROJECT="${1:?usage: make_zip.sh PROJECT [VARIANT]}"
VARIANT="${2:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRAMEWORK="$ROOT/src/autograder"
PROJECTS="$ROOT/projects"
case "$PROJECT" in tests|solutions) echo "not a project: $PROJECT" >&2; exit 1 ;; esac
[ -d "$PROJECTS/$PROJECT" ] || { echo "no such project: $PROJECT" >&2; exit 1; }

NAME="$PROJECT${VARIANT:+-$VARIANT}"
STAGE="$ROOT/build/$NAME"
rm -rf "$STAGE" && mkdir -p "$STAGE/autograder" "$STAGE/projects" "$ROOT/dist"

cp "$FRAMEWORK"/gradescope/{run_autograder,setup.sh,requirements.txt,pytest.ini} "$STAGE/"
cp "$ROOT/conftest.py" "$STAGE/"
cp "$FRAMEWORK"/*.py "$STAGE/autograder/"
cp -R "$PROJECTS/$PROJECT" "$STAGE/projects/"
echo "$PROJECT" > "$STAGE/PROJECT"
[ -n "$VARIANT" ] && echo "$VARIANT" > "$STAGE/VARIANT"
find "$STAGE" -name __pycache__ -prune -exec rm -rf {} +
find "$STAGE" -name .DS_Store -delete
chmod +x "$STAGE/run_autograder" "$STAGE/setup.sh"

rm -f "$ROOT/dist/$NAME.zip"
(cd "$STAGE" && zip -qr "$ROOT/dist/$NAME.zip" .)
echo "wrote dist/$NAME.zip"
