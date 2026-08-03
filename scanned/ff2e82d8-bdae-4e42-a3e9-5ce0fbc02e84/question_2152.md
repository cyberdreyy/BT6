# Q2152: createBuildVolume ownership changes upgrade trust of attacker files

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createBuildVolume` apply ownership or mode changes to attacker-selected files so later phases treat them as trusted?

## Target
- File/function: executors/docker/docker.go: createBuildVolume
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, mode, ownership, and colliding mounted files
- Exploit idea: use mount-related ownership changes to upgrade attacker content
- Invariant to test: mount-related ownership changes must not elevate attacker-controlled files beyond their trust boundary
- Expected Immunefi impact: stronger-context execution or later-phase hijack
- Fast validation: place attacker files on colliding mount paths and verify ownership changes do not upgrade trust
