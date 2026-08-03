# Q2425: hasExistingContainer auth state persists into an unrelated later job

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `hasExistingContainer` leave registry, helper, or workload auth state behind for a later unrelated or higher-trust job?

## Target
- File/function: executors/docker/docker_command.go: hasExistingContainer
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, repeated jobs on one runner and auth-related inputs
- Exploit idea: persist auth state beyond the current job boundary
- Invariant to test: job-derived auth state must not survive into later trust boundaries
- Expected Immunefi impact: cross-job credential misuse or wrong-image access
- Fast validation: run sequential jobs with different auth and verify no state leaks forward
