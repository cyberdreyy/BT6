# Q2388: runContainer cleanup leaves reusable lower-trust state

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `runContainer` fail cleanup in a way that leaves lower-trust state for a later protected or unrelated job to reuse?

## Target
- File/function: executors/docker/docker_command.go: runContainer
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, cleanup timing and repeated jobs
- Exploit idea: retain workload state after job end and rely on later reuse
- Invariant to test: cleanup must prevent lower-trust workload state from crossing into later jobs
- Expected Immunefi impact: cross-job state persistence or protected-boundary break
- Fast validation: leave hostile workload state behind and verify later jobs do not reuse it
