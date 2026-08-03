# Q2079: writeClearWorktreeScript path normalization mismatch bypasses checks

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `writeClearWorktreeScript` validate one path spelling but operate on another equivalent path after normalization differences?

## Target
- File/function: shells/abstract.go: writeClearWorktreeScript
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, slash, backslash, case, or drive aliases
- Exploit idea: pass validation on one representation and execute on another equivalent path
- Invariant to test: validation and final path use must agree on one canonical in-root path
- Expected Immunefi impact: cross-job tampering or stronger-context file overwrite
- Fast validation: exercise path aliases and verify consistent canonicalization
