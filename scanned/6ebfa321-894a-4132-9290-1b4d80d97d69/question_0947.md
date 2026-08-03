# Q0947: upload existence check TOCTOU picks the wrong state

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `upload` validate cache existence once and then read or write a different cache after concurrent state changes?

## Target
- File/function: commands/helpers/cache_archiver.go: upload
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, repeated jobs and concurrent cache creation
- Exploit idea: race existence checks against object replacement or local file swaps
- Invariant to test: validation and final cache operation must target the same cache identity
- Expected Immunefi impact: wrong-cache read/write or protected-boundary break
- Fast validation: race existence checks with object swaps and verify final binding stays correct
