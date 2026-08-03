# Q0640: artifactDownloadStateFromResponse restored file becomes executable or sourced later

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `artifactDownloadStateFromResponse` restore a file onto a path that later stages execute or source without re-establishing trust?

## Target
- File/function: network/gitlab.go: artifactDownloadStateFromResponse
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, filenames that collide with sourced or executed later-stage files
- Exploit idea: place attacker content where later runner-generated steps implicitly trust it
- Invariant to test: restore must not populate later-executed or later-sourced trusted paths
- Expected Immunefi impact: stronger-context execution or secret exposure
- Fast validation: restore onto later-stage script or env paths and verify later stages do not execute attacker bytes
