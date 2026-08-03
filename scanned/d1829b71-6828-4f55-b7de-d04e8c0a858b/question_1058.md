# Q1058: primaryPresignedExists lower-trust cache survives cleanup into later jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `primaryPresignedExists` leave lower-trust cache state on disk long enough for a later protected or unrelated job to consume it?

## Target
- File/function: commands/helpers/cache_archiver.go: primaryPresignedExists
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, cleanup timing and repeated jobs
- Exploit idea: retain local cache residue after the job ends and have later jobs trust it
- Invariant to test: cache cleanup must prevent lower-trust state from crossing into later jobs
- Expected Immunefi impact: cross-job state persistence or protected-boundary break
- Fast validation: leave hostile local cache state behind and verify later jobs ignore or delete it
