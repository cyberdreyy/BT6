# Q1309: Resolve submodule credentials follow attacker-chosen URLs

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `Resolve` apply submodule or nested-fetch credentials to attacker-controlled URLs or paths?

## Target
- File/function: common/secrets.go: Resolve
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, submodule URLs, nested repo paths, and repo-controlled config
- Exploit idea: cause credential-scoped git config to cover attacker-chosen submodule targets
- Invariant to test: submodule credential scope must stay limited to the intended GitLab target
- Expected Immunefi impact: token disclosure or cross-project unauthorized access
- Fast validation: use attacker-controlled submodule targets and verify credentials are not applied
