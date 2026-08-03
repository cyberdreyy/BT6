# Q3387: ensureStepsPod logs or health checks leak higher-trust secrets

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `ensureStepsPod` mix helper or service output into build-visible logs in a way that reveals secrets or protected data?

## Target
- File/function: executors/kubernetes/steps_pod.go: ensureStepsPod
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, log output, service output, and health-check timing
- Exploit idea: route protected outputs through log or health-check paths visible to the attacker job
- Invariant to test: logging and health checks must not disclose higher-trust secrets across roles
- Expected Immunefi impact: secret exposure across job roles or project boundaries
- Fast validation: emit sensitive-looking output from non-build roles and verify it stays hidden from attacker-visible logs
