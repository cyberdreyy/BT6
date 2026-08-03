# Q3105: createBuildAndHelperContainers auth state persists into an unrelated later job

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `createBuildAndHelperContainers` leave registry, helper, or workload auth state behind for a later unrelated or higher-trust job?

## Target
- File/function: executors/kubernetes/kubernetes.go: createBuildAndHelperContainers
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, repeated jobs on one runner and auth-related inputs
- Exploit idea: persist auth state beyond the current job boundary
- Invariant to test: job-derived auth state must not survive into later trust boundaries
- Expected Immunefi impact: cross-job credential misuse or wrong-image access
- Fast validation: run sequential jobs with different auth and verify no state leaks forward
