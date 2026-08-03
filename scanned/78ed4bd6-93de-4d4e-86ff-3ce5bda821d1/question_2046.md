# Q2046: writeGetSourcesScript cleanup path escapes the assigned roots

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `writeGetSourcesScript` derive a cleanup target that escapes the assigned root and removes or rewrites unrelated runner state?

## Target
- File/function: shells/abstract.go: writeGetSourcesScript
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, cleanup paths, slash variants, and relative segments
- Exploit idea: turn cleanup into a cross-boundary file operation
- Invariant to test: cleanup must remain confined to current job-owned paths
- Expected Immunefi impact: cross-job state tampering
- Fast validation: drive cleanup with escaping paths and verify only job-owned files are touched
