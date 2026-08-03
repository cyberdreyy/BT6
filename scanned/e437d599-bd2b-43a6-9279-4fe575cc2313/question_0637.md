# Q0637: artifactDownloadStateFromResponse hidden entries bypass restore filters

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `artifactDownloadStateFromResponse` accept hidden or special-looking entry names that bypass restore filters but later land on trusted paths?

## Target
- File/function: network/gitlab.go: artifactDownloadStateFromResponse
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, hidden names, dotfiles, and special prefixes
- Exploit idea: hide dangerous paths behind filtering assumptions
- Invariant to test: restore filters must treat hidden and special names with the same containment rules
- Expected Immunefi impact: secret exposure or trusted-config overwrite
- Fast validation: restore hidden control-style names and confirm they are blocked or isolated
