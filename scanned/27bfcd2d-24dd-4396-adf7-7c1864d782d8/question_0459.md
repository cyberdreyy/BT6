# Q0459: normalizeArgs compressed body provider reads a mutated file version

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `normalizeArgs` build its upload body from a file that changed after validation so the transmitted bytes do not match the trusted selection step?

## Target
- File/function: commands/helpers/artifacts_uploader.go: normalizeArgs
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, file mutation between validation and compression
- Exploit idea: change the archive input after validation but before the body provider reads it
- Invariant to test: validated file identity and transmitted bytes must remain bound
- Expected Immunefi impact: artifact poisoning or wrong-file disclosure
- Fast validation: mutate a selected file after validation and verify upload body generation detects it
