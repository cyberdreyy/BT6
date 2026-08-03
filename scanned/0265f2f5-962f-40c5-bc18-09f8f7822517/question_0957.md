# Q0957: upload upload and restore compute different namespaces

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `upload` derive one namespace on upload and a different namespace on restore for the same visible key?

## Target
- File/function: commands/helpers/cache_archiver.go: upload
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, the same visible key used across upload and restore
- Exploit idea: desynchronize key-to-namespace mapping across the two directions
- Invariant to test: upload and restore must map the same visible key to the same exact namespace
- Expected Immunefi impact: cache confusion and cross-job state poisoning
- Fast validation: round-trip one key through upload and restore and verify the mapping is identical
