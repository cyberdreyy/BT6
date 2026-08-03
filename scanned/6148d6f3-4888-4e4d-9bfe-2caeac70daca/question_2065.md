# Q2065: writeClearWorktreeScript temp file or script path collides with trusted files

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `writeClearWorktreeScript` choose a temp or script path that collides with an existing trusted file consumed by later stages?

## Target
- File/function: shells/abstract.go: writeClearWorktreeScript
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, colliding temp names and path aliases
- Exploit idea: force temp naming to reuse an attacker-controlled or already-trusted path
- Invariant to test: generated temp and script files must not collide with existing trusted files
- Expected Immunefi impact: later-stage hijack or secret exposure
- Fast validation: pre-place colliding files and verify generated paths remain unique and confined
