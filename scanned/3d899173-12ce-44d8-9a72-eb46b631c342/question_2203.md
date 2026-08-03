# Q2203: createVolumes cache and build volume paths collide

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createVolumes` place cache and build data onto one shared path so lower-trust cache state overwrites trusted build state?

## Target
- File/function: executors/docker/docker.go: createVolumes
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, cache paths, build paths, and repeated jobs
- Exploit idea: force two trust boundaries onto one mounted path
- Invariant to test: cache and build mounts must remain distinct across one job and across jobs
- Expected Immunefi impact: cross-job state poisoning or stronger-context overwrite
- Fast validation: configure colliding cache and build paths and verify separation
