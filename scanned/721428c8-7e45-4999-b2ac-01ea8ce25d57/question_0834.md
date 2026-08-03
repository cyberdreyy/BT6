# Q0834: getCacheDownloadURLAndEnv restore extracts cache outside the assigned root

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `getCacheDownloadURLAndEnv` restore cache contents outside the assigned root and replace trusted files used by later stages?

## Target
- File/function: shells/abstract.go: getCacheDownloadURLAndEnv
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache keys, fallback keys, cache paths, local cache files, and repeated jobs on one runner, crafted archive contents
- Exploit idea: deliver a cache archive whose extraction breaks root containment
- Invariant to test: cache restore must remain inside the assigned build/cache roots
- Expected Immunefi impact: path-root escape and later stronger-context overwrite
- Fast validation: restore a crafted cache archive and verify nothing escapes the assigned roots
