# Q2890: ensurePodsConfigured credentials or files prepared for one workload hit another

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `ensurePodsConfigured` prepare files or credentials for one workload and then apply them to another workload after state changes?

## Target
- File/function: executors/kubernetes/kubernetes.go: ensurePodsConfigured
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, mutable workload state and reused helper files
- Exploit idea: shift the effective target after preparing sensitive state
- Invariant to test: prepared sensitive state must remain bound to one exact workload
- Expected Immunefi impact: secret exposure or stronger-context execution
- Fast validation: change workload identity after setup and verify prepared state is not reused elsewhere
