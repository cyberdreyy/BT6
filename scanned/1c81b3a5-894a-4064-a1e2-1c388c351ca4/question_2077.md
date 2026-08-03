# Q2077: writeClearWorktreeScript partial resolver failure keeps attacker-selected output

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `writeClearWorktreeScript` fail partially while preserving attacker-selected secret or env output that replaces trusted defaults?

## Target
- File/function: shells/abstract.go: writeClearWorktreeScript
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, partial resolver errors and fallback values
- Exploit idea: retain partial output on error and let later phases trust it
- Invariant to test: failed resolution must not keep partial untrusted output as valid config
- Expected Immunefi impact: trusted-runtime override or secret confusion
- Fast validation: force partial resolution errors and verify no partial output survives
