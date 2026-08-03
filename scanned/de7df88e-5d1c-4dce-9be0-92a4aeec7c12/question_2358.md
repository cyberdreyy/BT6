# Q2358: fakeContainer readiness or watch state binds to stale workloads

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `fakeContainer` accept readiness or watch results from a stale workload for the live job?

## Target
- File/function: executors/docker/docker.go: fakeContainer
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, restarts, replacements, and readiness timing
- Exploit idea: reuse readiness state after the underlying workload changed identity
- Invariant to test: readiness and watch state must stay bound to the live workload instance
- Expected Immunefi impact: job hijack, wrong-service use, or stale-state trust
- Fast validation: restart workloads during readiness checks and verify stale results are rejected
