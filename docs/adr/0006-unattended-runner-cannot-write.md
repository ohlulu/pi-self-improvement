# The unattended runner cannot write files

The scheduled fixloop runner invokes `pi -p` with `--tools read,grep,find,ls`. It has no
shell and no write access. It produces a structured triage result on stdout; a
deterministic host-side writer — ordinary code, not a model — is the only thing that
appends to the output root. Files outside the output root are modified exclusively by the
interactive learn-loop skill, after a human approves.

This is the mechanism behind the project's headline promise, "It never changes anything on
its own." An earlier draft gave the unattended runner `read,grep,find,ls,write,edit` on the
reasoning that removing `bash` made it safe. It does not: `write` and `edit` are not
path-constrained, so an unsupervised model retained the ability to edit source files and
skills — exactly the class of change the approval gate exists to prevent.

If someone later wants the daily pass to be more useful, the tempting move is to hand it
`write` again. That is the move this record exists to stop. Extend the host-side writer
instead; it is deterministic and its reachable paths can be read off the code.
