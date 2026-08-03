# Q2527: resumeServices logs or health checks leak higher-trust secrets

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `resumeServices` mix helper or service output into build-visible logs in a way that reveals secrets or protected data?

## Target
- File/function: executors/docker/services.go: resumeServices
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, log output, service output, and health-check timing
- Exploit idea: route protected outputs through log or health-check paths visible to the attacker job
- Invariant to test: logging and health checks must not disclose higher-trust secrets across roles
- Expected Immunefi impact: secret exposure across job roles or project boundaries
- Fast validation: emit sensitive-looking output from non-build roles and verify it stays hidden from attacker-visible logs
