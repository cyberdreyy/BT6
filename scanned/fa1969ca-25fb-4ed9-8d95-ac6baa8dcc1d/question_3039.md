# Q3039: setupBuildPod cleanup acts on attacker-selected aliases

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `setupBuildPod` clean up an attacker-selected alias so the wrong workload or state is removed while hostile residue remains?

## Target
- File/function: executors/kubernetes/kubernetes.go: setupBuildPod
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, alias paths, colliding names, and cleanup timing
- Exploit idea: redirect cleanup to the wrong workload or path and keep attacker residue
- Invariant to test: cleanup must target only the current job workload and all of its state
- Expected Immunefi impact: cross-job tampering or persistent hostile state
- Fast validation: race cleanup with aliases and verify only the intended workload state changes
