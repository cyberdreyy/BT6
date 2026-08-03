# Q2071: writeClearWorktreeScript resolved secrets overwrite trusted runtime env

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `writeClearWorktreeScript` let secret-derived or file-derived values overwrite trusted runtime variables such as auth, cache, or helper settings?

## Target
- File/function: shells/abstract.go: writeClearWorktreeScript
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, variable names that collide with trusted runtime keys
- Exploit idea: replace trusted runtime config with attacker-selected resolved values
- Invariant to test: resolved variables must not override trusted runner-runtime settings across trust boundaries
- Expected Immunefi impact: secret exposure, wrong-target auth, or later job hijack
- Fast validation: resolve colliding variable names and verify protected runtime keys remain unchanged
