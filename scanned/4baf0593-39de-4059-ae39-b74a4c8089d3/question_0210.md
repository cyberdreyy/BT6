# Q0210: updateFileMetadata restore into helper or temp files consumed later

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `updateFileMetadata` place files where later helper binaries, env files, or temp scripts read them as trusted inputs?

## Target
- File/function: commands/helpers/archive/tarzstd/tarzstd_extractor.go: updateFileMetadata
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, names that collide with temp or helper paths
- Exploit idea: restore attacker files onto helper-consumed paths before later stages run
- Invariant to test: helper and temp paths must remain isolated from restored attacker content
- Expected Immunefi impact: secret exposure or stronger-context execution
- Fast validation: restore onto likely helper/temp names and verify later stages do not consume attacker bytes
