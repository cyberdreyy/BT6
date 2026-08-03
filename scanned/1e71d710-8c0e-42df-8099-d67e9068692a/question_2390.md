# Q2390: runContainer credentials or files prepared for one workload hit another

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `runContainer` prepare files or credentials for one workload and then apply them to another workload after state changes?

## Target
- File/function: executors/docker/docker_command.go: runContainer
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, mutable workload state and reused helper files
- Exploit idea: shift the effective target after preparing sensitive state
- Invariant to test: prepared sensitive state must remain bound to one exact workload
- Expected Immunefi impact: secret exposure or stronger-context execution
- Fast validation: change workload identity after setup and verify prepared state is not reused elsewhere
