# Q1205: resolveGoCloudSource local cache archive path collision on disk

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `resolveGoCloudSource` place two trust boundaries onto the same local cache archive path on disk?

## Target
- File/function: commands/helpers/cache_extractor.go: resolveGoCloudSource
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, colliding local paths and repeated jobs
- Exploit idea: target the same local archive file from unrelated jobs or refs
- Invariant to test: local cache files must stay unique per job trust boundary
- Expected Immunefi impact: cross-job cache poisoning or wrong-cache reuse
- Fast validation: run colliding jobs and verify they never share one local cache file
