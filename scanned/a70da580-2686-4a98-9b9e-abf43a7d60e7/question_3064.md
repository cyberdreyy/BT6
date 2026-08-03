# Q3064: preparePodServices image or service normalization selects the wrong workload

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `preparePodServices` resolve two distinct image or service identifiers onto one trusted workload or auth scope?

## Target
- File/function: executors/kubernetes/kubernetes.go: preparePodServices
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, visually similar image or service references
- Exploit idea: collapse distinct workload identities through normalization or caching
- Invariant to test: image and service identity must remain exact across auth and workload selection
- Expected Immunefi impact: wrong-image execution or cross-job auth reuse
- Fast validation: use colliding image or service identifiers and verify exact identity binding
