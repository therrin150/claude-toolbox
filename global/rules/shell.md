# Shell

My shell is zsh. To print a separator between commands, use `printf '%s\n' '-----'`, never `echo =====`: zsh expands
a word starting with `=` to a command path, so `echo =====` fails with `==== not found` and the rest of the line never
runs.
