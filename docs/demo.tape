# VHS tape for docs/demo.gif. Render with scripts/make_demo_gif.sh, which
# fills in RECORDING, OUTDIR and SPEED:
#   scripts/make_demo_gif.sh demo/recorded_runs.json docs
# The terminal shows ngpvan-mcp-replay playing back demo/recorded_runs.json,
# a session recorded with ngpvan-mcp-record.

Output "OUTDIR/demo.gif"
Output "OUTDIR/demo.mp4"

Set Shell "bash"
Set FontSize 13
Set Width 1280
Set Height 780
Set Padding 16
Set TypingSpeed 30ms
Set Theme "Builtin Dark"
Set WindowBar Colorful
Set Framerate 8

Hide
Type "export PS1='$ ' COLUMNS=124 && clear"
Enter
Show

Type "ngpvan-mcp-replay RECORDING --width 124 --speed SPEED"
Sleep 400ms
Enter
Sleep 2s
Wait@180s /^\$\s*$/
Sleep 3s
