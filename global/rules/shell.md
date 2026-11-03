# Shell

My shell is zsh. To print a separator between commands, use `printf '%s\n' '-----'`, never `echo =====`: zsh expands
a word starting with `=` to a command path, so `echo =====` fails with `==== not found` and the rest of the line never
runs.

zsh doesn't word-split an unquoted variable, so a command held in one (`cmd="python3 x.py"; $cmd`) runs as a single
word and fails. Write the command out, or use a function or an array.
