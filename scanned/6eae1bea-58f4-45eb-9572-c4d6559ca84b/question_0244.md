# Q0244: Archive repo-control file inclusion

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` package `.git`, hook, or config files that later restore into trusted repo-control paths?

## Target
- File/function: commands/helpers/archive/fastzip/zip_fastzip_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, repo-control files and hidden paths
- Exploit idea: archive control files that alter later git or helper behavior after restore
- Invariant to test: repo-control files must not be exported into trust-boundary-crossing archives
- Expected Immunefi impact: protected-ref escalation or credential misuse
- Fast validation: include repo-control files and confirm packaging rejects or strips them
