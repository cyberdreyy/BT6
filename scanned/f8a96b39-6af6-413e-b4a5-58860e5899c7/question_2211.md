# Q2211: createVolumes mount prepared for one job is reused by another

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createVolumes` prepare mount state for one job and then attach it to a different job after state changes?

## Target
- File/function: executors/docker/docker.go: createVolumes
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, rapid sequential jobs and shared local state
- Exploit idea: keep local mount state alive until a later job inherits it
- Invariant to test: mounted local state must remain bound to the creating job only
- Expected Immunefi impact: cross-job hijack or stale-state reuse
- Fast validation: run sequential jobs and verify mount state is isolated per job
