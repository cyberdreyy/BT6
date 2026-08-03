# Q2686: GetDockerImage helper or auth state survives into later jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `GetDockerImage` leave helper or registry auth state behind for a later unrelated or higher-trust job?

## Target
- File/function: executors/docker/internal/pull/manager.go: GetDockerImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, repeated jobs and merged auth inputs
- Exploit idea: persist auth-related state beyond the current job
- Invariant to test: job-derived auth state must not survive into later trust boundaries
- Expected Immunefi impact: cross-job credential misuse or wrong-image access
- Fast validation: run sequential jobs with different auth and verify no helper or registry state leaks forward
