# Q2210: createVolumes restored cache overlays a trusted mount target

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createVolumes` overlay restored cache state onto a mounted path later sourced or executed by the build?

## Target
- File/function: executors/docker/docker.go: createVolumes
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, restored cache contents and shared mount paths
- Exploit idea: reuse one mounted path for cache state and trusted later-stage inputs
- Invariant to test: cache restoration must not overlay trusted mounted inputs
- Expected Immunefi impact: stronger-context execution or secret exposure
- Fast validation: restore cache over shared paths and verify later stages do not trust it
