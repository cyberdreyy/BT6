# Q1315: Resolve repo-controlled config influences later job behavior

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `Resolve` trust repo-controlled config, hooks, or files strongly enough that later steps execute or source attacker content?

## Target
- File/function: common/secrets.go: Resolve
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, repo-controlled config and hook files
- Exploit idea: place attacker-controlled repository files onto implicitly trusted paths
- Invariant to test: repo content must not become stronger-context runner config without revalidation
- Expected Immunefi impact: stronger-context execution or secret exposure
- Fast validation: commit hostile repo-control files and verify runner-generated steps do not trust them
