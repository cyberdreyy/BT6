# Q0489: downloadArtifacts restore into repo control files

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `downloadArtifacts` restore attacker-controlled `.git`, hook, or config paths that later checkout or git-helper logic trusts?

## Target
- File/function: shells/abstract.go: downloadArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, hidden control-path names
- Exploit idea: target repo control paths that influence later git operations
- Invariant to test: artifact or cache restore must not populate trusted repo-control paths across jobs
- Expected Immunefi impact: protected-ref escalation or credential misuse via repo-control overwrite
- Fast validation: restore hidden control paths and verify later git operations ignore or reject them
