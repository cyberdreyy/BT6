# Q0510: downloadAllArtifacts restore into helper or temp files consumed later

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `downloadAllArtifacts` place files where later helper binaries, env files, or temp scripts read them as trusted inputs?

## Target
- File/function: shells/abstract.go: downloadAllArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, names that collide with temp or helper paths
- Exploit idea: restore attacker files onto helper-consumed paths before later stages run
- Invariant to test: helper and temp paths must remain isolated from restored attacker content
- Expected Immunefi impact: secret exposure or stronger-context execution
- Fast validation: restore onto likely helper/temp names and verify later stages do not consume attacker bytes
