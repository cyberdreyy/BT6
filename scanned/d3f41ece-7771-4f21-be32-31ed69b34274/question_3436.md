# Q3436: setupStepsPod log or attach streams mix bytes across workloads

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `setupStepsPod` mix bytes from multiple workloads into one attacker-visible stream?

## Target
- File/function: executors/kubernetes/steps_pod.go: setupStepsPod
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, concurrent workload output and reconnect timing
- Exploit idea: cause stream multiplexing to lose workload ownership
- Invariant to test: log and attach streams must preserve workload ownership for every byte
- Expected Immunefi impact: secret exposure or output tampering
- Fast validation: emit distinct markers from multiple workloads and verify streams never mix them
