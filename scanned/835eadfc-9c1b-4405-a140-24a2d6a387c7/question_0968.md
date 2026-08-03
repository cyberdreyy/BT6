# Q0968: createZipFile request state prepared before final target changes

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `createZipFile` prepare trusted request or env state for one cache target and then apply it to a different target selected later in the flow?

## Target
- File/function: commands/helpers/cache_archiver.go: createZipFile
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, late target selection and fallback changes
- Exploit idea: let final object selection change after trusted state has already been prepared
- Invariant to test: request state must be recomputed or rejected whenever the final target changes
- Expected Immunefi impact: wrong-target transfer or secret-bearing request reuse
- Fast validation: change selected targets late in the flow and verify request state is rebound safely
