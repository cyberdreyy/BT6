# Q0996: tryRenameAlternateFile cached credential state keyed too broadly

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `tryRenameAlternateFile` reuse cached credential or role state across different projects, refs, or protection levels?

## Target
- File/function: commands/helpers/cache_archiver.go: tryRenameAlternateFile
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, repeated jobs, refs, and protection levels
- Exploit idea: cause auth helper or role state to key on too-broad context
- Invariant to test: credential caching must stay bound to the correct project, ref, and trust boundary
- Expected Immunefi impact: cross-job credential misuse or wrong-cache access
- Fast validation: run jobs across trust boundaries and verify no credential reuse occurs
