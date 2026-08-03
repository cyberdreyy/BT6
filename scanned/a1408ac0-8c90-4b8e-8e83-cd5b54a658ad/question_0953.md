# Q0953: upload upload reads outside the intended cache root

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `upload` upload a file outside the intended cache root by abusing path aliasing or colliding local archive names?

## Target
- File/function: commands/helpers/cache_archiver.go: upload
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, colliding archive names and path aliases
- Exploit idea: steer upload to a local file that is not the current job cache archive
- Invariant to test: cache upload must only read from the current job cache archive path
- Expected Immunefi impact: secret-bearing file disclosure or wrong-object mutation
- Fast validation: prepare colliding local files and verify upload stays pinned to the correct archive
