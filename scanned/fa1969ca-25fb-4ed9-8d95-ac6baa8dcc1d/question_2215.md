# Q2215: createVolumes build volume overlaps artifact or output paths

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createVolumes` mount build state onto a path that collides with artifact staging or other trusted output state?

## Target
- File/function: executors/docker/docker.go: createVolumes
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, overlapping build and output paths
- Exploit idea: merge build and trusted output state through one mounted target
- Invariant to test: build mounts must stay isolated from artifact and output staging paths
- Expected Immunefi impact: output corruption or cross-stage tampering
- Fast validation: set overlapping paths and verify mounted build state cannot land on artifact paths
