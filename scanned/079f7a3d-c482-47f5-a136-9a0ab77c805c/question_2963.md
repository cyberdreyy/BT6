# Q2963: saveScriptOnEmptyDir mount or workspace escape in supported non-privileged mode

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `saveScriptOnEmptyDir` mount or reuse a path outside the intended workspace, cache, or temp roots in a supported non-privileged setup?

## Target
- File/function: executors/kubernetes/kubernetes.go: saveScriptOnEmptyDir
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, path aliases, cache residue, and workspace state
- Exploit idea: steer mounts or workspace state onto external runner-owned paths
- Invariant to test: non-privileged job state must remain confined to assigned roots
- Expected Immunefi impact: supported non-privileged isolation break or cross-job tampering
- Fast validation: prepare alias paths and verify mounted state stays within assigned roots
