# Recall Demo — recorded from the local CLI
# Install: brew install vhs
# Record:  vhs docs/recall-demo.tape
#
# Prerequisites:
#   - Run from the recall repository checkout.
#   - .venv exists and has recall installed.
#   - For semantic search: pip install -e ".[semantic]" && recall index --semantic

Output docs/recall-demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1800
Set Height 700
Set Padding 10
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms

Hide
Type "cd /Users/viggy28/source/github/viggy28/recall"
Enter
Sleep 300ms
Type "export PATH=/opt/homebrew/bin:$PATH"
Enter
Sleep 300ms
Type "export PS1='viggy@mac recall % '"
Enter
Sleep 300ms
Ctrl+L
Show

Type "./.venv/bin/recall index --stats"
Enter
Sleep 1.5s

Type "./.venv/bin/recall search 'migration safe not safe' --limit 3 --no-index"
Enter
Sleep 3s

Ctrl+L
Type "./.venv/bin/recall search --semantic 'local memory tool for coding agent sessions' --limit 3 --no-index"
Enter
Sleep 3s

Ctrl+L
Type "./.venv/bin/recall context list"
Enter
Sleep 1s
Type "./.venv/bin/recall context show recall"
Enter
Sleep 3s

Ctrl+L
Type "npm view recall-pi version description"
Enter
Sleep 1.5s
Type "# Pi extension: /recall, recall_search, recall_context"
Sleep 2s
