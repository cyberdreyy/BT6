# Q0518: downloadAllArtifacts error-path cleanup follows attacker alias

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `downloadAllArtifacts` hit an error path where cleanup follows attacker-controlled aliases and removes or rewrites files outside the intended root?

## Target
- File/function: shells/abstract.go: downloadAllArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, failure timing, symlink aliases, and renamed directories
- Exploit idea: steer cleanup into attacker-selected aliases after a forced failure
- Invariant to test: failure cleanup must only touch paths proven to belong to the current restore root
- Expected Immunefi impact: cross-job tampering through cleanup path confusion
- Fast validation: induce a restore failure and verify cleanup never touches external paths
