# Q2344: fakeContainer image or service normalization selects the wrong workload

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `fakeContainer` resolve two distinct image or service identifiers onto one trusted workload or auth scope?

## Target
- File/function: executors/docker/docker.go: fakeContainer
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, visually similar image or service references
- Exploit idea: collapse distinct workload identities through normalization or caching
- Invariant to test: image and service identity must remain exact across auth and workload selection
- Expected Immunefi impact: wrong-image execution or cross-job auth reuse
- Fast validation: use colliding image or service identifiers and verify exact identity binding
