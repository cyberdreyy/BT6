# Q2144: createBuildVolume temporary volume collides with trusted state

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createBuildVolume` choose or reuse a temporary volume path that collides with trusted helper or script state?

## Target
- File/function: executors/docker/docker.go: createBuildVolume
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, temporary names and pre-existing files
- Exploit idea: make temporary mount naming non-unique so it lands on a trusted path
- Invariant to test: temporary mount paths must be unique and isolated per job
- Expected Immunefi impact: later-stage hijack or helper-state overwrite
- Fast validation: pre-place colliding files and verify temporary mount paths stay unique
