# Q0487: downloadArtifacts metadata upgrade that turns restored files into trusted code

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `downloadArtifacts` preserve executable bits, uid/gid, or timestamps that cause a later stronger-context step to treat attacker content as trusted code or config?

## Target
- File/function: shells/abstract.go: downloadArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, executable mode, uid/gid, and timestamps
- Exploit idea: use metadata restore to upgrade trust of attacker-provided files
- Invariant to test: restored metadata must not elevate trust of attacker content beyond the job boundary
- Expected Immunefi impact: stronger-context execution or protected data exposure
- Fast validation: restore attacker files with hostile metadata and verify no stronger-context trust change occurs
