# Q3255: waitForServices helper bootstrap writes files later trusted by the build phase

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `waitForServices` write helper or bootstrap files that the build phase later trusts without re-establishing ownership or origin?

## Target
- File/function: executors/kubernetes/kubernetes.go: waitForServices
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, shared files between helper and build phases
- Exploit idea: smuggle attacker-influenced files across a helper/build trust boundary
- Invariant to test: build phase must not trust helper-written attacker content without explicit rebinding
- Expected Immunefi impact: stronger-context execution or build-phase hijack
- Fast validation: place attacker-influenced helper files and verify the build phase does not trust them
