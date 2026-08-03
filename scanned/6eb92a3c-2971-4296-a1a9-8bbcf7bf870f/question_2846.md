# Q2846: setupPodLegacy terminal, proxy, or exec reaches the wrong live workload

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `setupPodLegacy` attach, proxy, or exec against the wrong live workload for the current job?

## Target
- File/function: executors/kubernetes/kubernetes.go: setupPodLegacy
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, reconnect timing and live workload changes
- Exploit idea: desynchronize live-session selection from the intended workload
- Invariant to test: interactive and exec paths must stay bound to the live job workload
- Expected Immunefi impact: session hijack or wrong-workload access
- Fast validation: reconnect while workloads change and verify the session cannot switch targets
