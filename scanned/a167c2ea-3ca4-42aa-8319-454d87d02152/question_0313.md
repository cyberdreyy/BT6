# Q0313: Archive archive naming collision across refs or jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` choose an archive name or output path that collides across refs, stages, or jobs on the same runner?

## Target
- File/function: commands/helpers/archive/tarzstd/tarzstd_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, archive names, stage names, and repeated jobs
- Exploit idea: cause archives from different trust contexts to resolve to the same local output path
- Invariant to test: local archive outputs must stay unique per job and trust boundary
- Expected Immunefi impact: cross-job artifact confusion or poisoning
- Fast validation: run colliding jobs and verify distinct local archive outputs
