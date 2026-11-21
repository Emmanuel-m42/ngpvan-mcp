#!/usr/bin/env bash
# Render the terminal demo from a recording with VHS (charmbracelet/vhs).
#   scripts/make_demo_gif.sh [recording.json] [output dir] [speed]
# Needs vhs, ttyd and ffmpeg on PATH. Writes demo.gif and demo.mp4.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
# Shown on screen, so keep it relative to the repo when it's inside it.
recording="$(realpath --relative-base="$here" "${1:-$here/demo/recorded_runs.json}")"
outdir="$(realpath -m "${2:-$here/docs}")"
speed="${3:-1.0}"
mkdir -p "$outdir"

tape="$(mktemp --suffix=.tape)"
trap 'rm -f "$tape"' EXIT
sed -e "s|OUTDIR|$outdir|g" -e "s|RECORDING|$recording|g" -e "s|SPEED|$speed|g" "$here/docs/demo.tape" > "$tape"

# The tape runs the replay command from the project's environment.
export PATH="$here/.venv/bin:$PATH"
cd "$here"
vhs "$tape"

# VHS's own GIF runs past 5 MB once the text scrolls; rebuild it from the MP4 at
# 4 frames a second, 1000 px wide and a 32-colour palette, which stays readable.
ffmpeg -v error -y -i "$outdir/demo.mp4" \
  -vf "fps=4,scale=1000:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=32:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
  "$outdir/demo.small.gif"
mv "$outdir/demo.small.gif" "$outdir/demo.gif"
ls -l "$outdir/demo.gif" "$outdir/demo.mp4"
