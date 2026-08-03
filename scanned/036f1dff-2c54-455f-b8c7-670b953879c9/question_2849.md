# Q2849: setupPodLegacy name collision reuses another job workload

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `setupPodLegacy` collide names, labels, or selectors so one job reuses another job’s pod, container, or mounted build state?

## Target
- File/function: executors/kubernetes/kubernetes.go: setupPodLegacy
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, colliding names, labels, or selectors
- Exploit idea: make workload identity non-unique across jobs or attempts
- Invariant to test: workload naming and selection must remain unique per live job
- Expected Immunefi impact: job hijack or cross-job secret exposure
- Fast validation: run colliding jobs and verify selectors never cross job boundaries
