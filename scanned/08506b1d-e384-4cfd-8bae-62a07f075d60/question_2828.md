# Q2828: runWithExecLegacy cleanup leaves reusable lower-trust state

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `runWithExecLegacy` fail cleanup in a way that leaves lower-trust state for a later protected or unrelated job to reuse?

## Target
- File/function: executors/kubernetes/kubernetes.go: runWithExecLegacy
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, cleanup timing and repeated jobs
- Exploit idea: retain workload state after job end and rely on later reuse
- Invariant to test: cleanup must prevent lower-trust workload state from crossing into later jobs
- Expected Immunefi impact: cross-job state persistence or protected-boundary break
- Fast validation: leave hostile workload state behind and verify later jobs do not reuse it
