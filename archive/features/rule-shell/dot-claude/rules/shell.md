# Shell

My shell is zsh. To print a separator between commands, use `printf '%s\n' '-----'`, never `echo =====`: zsh expands
a word starting with `=` to a command path, so `echo =====` fails with `==== not found` and the rest of the line never
runs.

zsh doesn't word-split an unquoted variable, so a command held in one (`cmd="python3 x.py"; $cmd`) runs as a single
word and fails. Write the command out, or use a function or an array.

The same goes for a list held in a variable: `f=$(find …); for x in $f` runs the loop once over the whole
newline-joined string, so `cat "$x"` fails. Never iterate an unquoted variable or substitution. Pipe instead
(`find … | while IFS= read -r x`), split explicitly (`for x in ${(f)f}`), or skip the loop (`find … -exec cat {} +`).
