# Q0229: openArchive restore into repo control files

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `openArchive` restore attacker-controlled `.git`, hook, or config paths that later checkout or git-helper logic trusts?

## Target
- File/function: commands/helpers/artifacts_downloader.go: openArchive
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, hidden control-path names
- Exploit idea: target repo control paths that influence later git operations
- Invariant to test: artifact or cache restore must not populate trusted repo-control paths across jobs
- Expected Immunefi impact: protected-ref escalation or credential misuse via repo-control overwrite
- Fast validation: restore hidden control paths and verify later git operations ignore or reject them
