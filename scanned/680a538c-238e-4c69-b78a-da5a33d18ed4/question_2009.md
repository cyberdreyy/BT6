# Q2009: bashDetectScript submodule credentials follow attacker-chosen URLs

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `bashDetectScript` apply submodule or nested-fetch credentials to attacker-controlled URLs or paths?

## Target
- File/function: shells/bash.go: bashDetectScript
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, submodule URLs, nested repo paths, and repo-controlled config
- Exploit idea: cause credential-scoped git config to cover attacker-chosen submodule targets
- Invariant to test: submodule credential scope must stay limited to the intended GitLab target
- Expected Immunefi impact: token disclosure or cross-project unauthorized access
- Fast validation: use attacker-controlled submodule targets and verify credentials are not applied
