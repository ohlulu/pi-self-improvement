# Python stdlib package, not a TypeScript pi extension

A tool for the pi ecosystem that is not written in pi's language looks like an oversight,
so it is worth stating: the miner is an offline batch job with no need for pi runtime
integration, and scheduling is launchd's problem, not pi's. Writing it as a TypeScript
extension would have bought nothing and cost a hand-translation of twenty-odd subtle
detector regexes from Python semantics to JavaScript semantics.

Python 3.10+, standard library only, zero third-party dependencies. The upstream semantics
we are porting are expressed as Python regexes, so a 1:1 rewrite is the form that best
preserves behavior.
