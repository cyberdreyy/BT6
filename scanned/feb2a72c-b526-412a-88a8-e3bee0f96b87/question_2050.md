# Q2050: writeGetSourcesScript checkout or include path escapes the workspace

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `writeGetSourcesScript` resolve checkout, include, or config paths outside the assigned checkout root and temp config roots?

## Target
- File/function: shells/abstract.go: writeGetSourcesScript
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, checkout and include paths
- Exploit idea: drive path resolution onto external or sibling runner paths
- Invariant to test: checkout and include paths must remain inside the assigned checkout root and temp config roots
- Expected Immunefi impact: cross-job tampering or helper-state overwrite
- Fast validation: supply escaping checkout/include paths and verify confinement
