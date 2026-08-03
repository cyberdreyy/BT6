# Q2069: writeClearWorktreeScript submodule credentials follow attacker-chosen URLs

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `writeClearWorktreeScript` apply submodule or nested-fetch credentials to attacker-controlled URLs or paths?

## Target
- File/function: shells/abstract.go: writeClearWorktreeScript
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, submodule URLs, nested repo paths, and repo-controlled config
- Exploit idea: cause credential-scoped git config to cover attacker-chosen submodule targets
- Invariant to test: submodule credential scope must stay limited to the intended GitLab target
- Expected Immunefi impact: token disclosure or cross-project unauthorized access
- Fast validation: use attacker-controlled submodule targets and verify credentials are not applied
