Output docs/recall-demo.gif

Require bash

Set Shell "bash"
Set FontSize 26
Set FontFamily "JetBrains Mono"
Set Width 1200
Set Height 675
Set Padding 28
Set Theme "Catppuccin Mocha"
Set TypingSpeed 18ms
Set PlaybackSpeed 1.25
Set Framerate 20
Set WindowBar Colorful

Hide
Type "cd /Users/viggy28/source/github/viggy28/recall"
Enter
Type "source docs/recall-demo-fixtures.sh"
Enter
Ctrl+L
Show

Type "recall index --stats"
Enter
Sleep 1.2s

Type "recall search migration safe not safe --limit 3"
Enter
Sleep 2s

Ctrl+L
Type "recall search --semantic local memory tool for coding agent sessions --limit 3"
Enter
Sleep 2s

Ctrl+L
Type "recall context list"
Enter
Sleep 900ms
Type "recall context show recall"
Enter
Sleep 2s

Ctrl+L
Type "pi install npm:recall-pi"
Enter
Sleep 1s
Type "/recall  # search sessions, attach contexts, update memory inside Pi"
Sleep 2s
