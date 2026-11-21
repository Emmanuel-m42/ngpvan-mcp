# VHS tape for docs/demo.gif. Render with scripts/make_demo_gif.sh, which
# fills in RECORDING and OUTDIR:
#   scripts/make_demo_gif.sh demo/recorded_runs.json docs
# The terminal shows ngpvan-mcp-replay playing back demo/recorded_runs.json,
# a session recorded with ngpvan-mcp-record.

Output "OUTDIR/demo.gif"
Output "OUTDIR/demo.mp4"

Set Shell "bash"
Set FontSize 15
Set Width 1180
Set Height 760
Set Padding 18
Set TypingSpeed 35ms
Set Theme "Builtin Dark"
Set WindowBar Colorful
Set Framerate 12

Hide
Type "export PS1='$ ' COLUMNS=104 && clear"
Enter
Show

Type "ngpvan-mcp-replay RECORDING --width 104 --speed SPEED"
Sleep 400ms
Enter
Wait@180s /\$ $/
Sleep 3s
