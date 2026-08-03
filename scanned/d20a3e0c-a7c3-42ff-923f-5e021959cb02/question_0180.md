# Q0180: Extract restored file becomes executable or sourced later

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `Extract` restore a file onto a path that later stages execute or source without re-establishing trust?

## Target
- File/function: commands/helpers/archive/ziplegacy/zip_legacy_extractor.go: Extract
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, filenames that collide with sourced or executed later-stage files
- Exploit idea: place attacker content where later runner-generated steps implicitly trust it
- Invariant to test: restore must not populate later-executed or later-sourced trusted paths
- Expected Immunefi impact: stronger-context execution or secret exposure
- Fast validation: restore onto later-stage script or env paths and verify later stages do not execute attacker bytes
