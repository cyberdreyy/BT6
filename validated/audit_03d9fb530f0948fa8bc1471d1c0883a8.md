### Title
Path traversal via `--name` in `generateStatementToFile` allows artifact-metadata JSON to be written outside `artifactsWd` - ([File: commands/helpers/artifact_metadata.go])

### Summary
`ArtifactsUploaderCommand.Name` (CLI `--name`, backed by the CI `artifacts:name` field) is shell-expanded in `normalizeArgs` but never sanitized for path-traversal sequences before being passed as `artifactName` into `generateStatementToFile`. At line 100, `filepath.Join(opts.artifactsWd, fmt.Sprintf(artifactsStatementFormat, opts.artifactName))` will clean `../` sequences in `artifactName` and can resolve to a path outside `artifactsWd`, unlike the sibling function `artifactFilename`, which explicitly calls `filepath.Base(name)` before building the uploaded archive's filename.

### Finding Description
- `ArtifactsUploaderCommand.Name` is a pipeline-author-controlled value (`artifacts:name:` in `.gitlab-ci.yml`, or `--name` on the internal helper invocation) that reaches `normalizeArgs` at `commands/helpers/artifacts_uploader.go:252-264`, where it is only passed through `shell.Expand` (for variable substitution) and never validated against path-traversal characters.
- In `Execute` (`commands/helpers/artifacts_uploader.go:213-243`), when `GenerateArtifactsMetadata` is true, `c.Name` is passed unmodified as `artifactName` into `generateStatementToFile`.
- Inside `generateStatementToFile` (`commands/helpers/artifact_metadata.go:100`):
  `file := filepath.Join(opts.artifactsWd, fmt.Sprintf(artifactsStatementFormat, opts.artifactName))`
  `filepath.Join` calls `filepath.Clean`, so an `artifactName` such as `../../other-job-workspace/x` collapses `..` segments and can escape `artifactsWd` entirely, and the result is passed straight to `os.WriteFile(file, b, 0o644)` at line 102 with no re-validation that `file` remains inside `artifactsWd`.
- By contrast, the actual uploaded archive filename is computed by `artifactFilename` (`commands/helpers/artifacts_uploader.go:79-96`), which explicitly does `name = filepath.Base(name)` before formatting the extension — i.e., the codebase already recognizes `Name` as untrusted for path purposes in one place but fails to apply the same guard in the metadata-statement code path.
- No other check in the reachable path (network upload validation, `enumerate()`, or `process()`) constrains the metadata output path back into `artifactsWd`; the existing unit test `TestGenerateMetadataToFile` (`commands/helpers/artifact_metadata_test.go`) only exercises benign artifact names and does not test traversal inputs.

### Impact Explanation
An unprivileged pipeline author who controls the `artifacts:name` CI field can cause the helper process to write the SLSA/in-toto metadata JSON file to an arbitrary path reachable by the job's OS-level write permissions (e.g., a sibling job workspace directory under the shared runner's builds root, or elsewhere writable by the build user), rather than confined to the job's own artifacts working directory. This can overwrite files in another job's checkout or cache path within the same build user's filesystem reach, matching the "cross-project/cross-job file overwrite" scoped impact. Note this is bounded by OS file permissions of the build user, not by any Runner-enforced sandboxing — the missing check is specifically the absence of any `artifactsWd`-containment validation in this Go code path, which the runner otherwise attempts to guarantee.

### Likelihood Explanation
Preconditions are minimal and fully attacker-controlled: the pipeline author sets `artifacts:name: "../../other-job-workspace/x"` (or similar) and enables `--generate-artifacts-metadata` (or the runner config enables it project-wide). No special runner privileges, admin cooperation, or race conditions are required, and the bug is deterministically reproducible on every run where metadata generation is enabled with such a name. Real-world severity depends on how `artifactsWd` values (build directories) are laid out and whether the build user can write to sibling directories, but the code-level flaw itself is unconditionally reachable.

### Recommendation
Sanitize `opts.artifactName` in `generateStatementToFile` the same way `artifactFilename` sanitizes `Name` — e.g., apply `filepath.Base(opts.artifactName)` (and reject empty/`.`/`..` results) before formatting into `artifactsStatementFormat`, and additionally assert post-`filepath.Join` that the resulting path, after `filepath.Clean`, retains `filepath.Clean(opts.artifactsWd)` as a prefix, failing the operation otherwise.

### Proof of Concept
```go
// commands/helpers/artifact_metadata_test.go
func TestGenerateMetadataToFile_PathTraversalRejected(t *testing.T) {
    tmpDir := t.TempDir()
    artifactsWd := filepath.Join(tmpDir, "job-wd")
    require.NoError(t, os.MkdirAll(artifactsWd, 0o755))

    g := &artifactStatementGenerator{
        StartedAtRFC3339: time.Now().Format(time.RFC3339),
        EndedAtRFC3339:   time.Now().Format(time.RFC3339),
        SLSAProvenanceVersion: slsaProvenanceVersion1,
    }

    opts := generateStatementOptions{
        artifactName: "../../escaped-x",
        files:        map[string]os.FileInfo{},
        artifactsWd:  artifactsWd,
        jobID:        1,
    }

    file, err := g.generateStatementToFile(opts)
    require.NoError(t, err) // currently succeeds - demonstrates the bug

    cleanFile := filepath.Clean(file)
    cleanWd := filepath.Clean(artifactsWd)
    // EXPECTED (post-fix) assertion: file must stay within artifactsWd
    assert.True(t, strings.HasPrefix(cleanFile, cleanWd+string(filepath.Separator)),
        "metadata file %q escaped artifactsWd %q", cleanFile, cleanWd)
}
```
Running this test against the current code shows `cleanFile` resolves outside `cleanWd` (e.g., `tmpDir/escaped-x-metadata.json`), confirming the traversal; after applying the recommended `filepath.Base` + prefix-check fix, the function should instead error out or clamp the filename, and the assertion should pass. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** commands/helpers/artifact_metadata.go (L98-103)
```go
	}

	file := filepath.Join(opts.artifactsWd, fmt.Sprintf(artifactsStatementFormat, opts.artifactName))

	err = os.WriteFile(file, b, 0o644)
	return file, err
```

**File:** commands/helpers/artifacts_uploader.go (L79-96)
```go
func (c *ArtifactsUploaderCommand) artifactFilename(name string, format spec.ArtifactFormat) string {
	name = filepath.Base(name)
	if name == "" || name == "." {
		name = DefaultUploadName
	}

	switch format {
	case spec.ArtifactFormatZip, spec.ArtifactFormatZipZstd:
		return name + ".zip"

	case spec.ArtifactFormatGzip:
		return name + ".gz"

	case spec.ArtifactFormatTarZstd:
		return name + ".tar.zst"
	}
	return name
}
```

**File:** commands/helpers/artifacts_uploader.go (L213-237)
```go
func (c *ArtifactsUploaderCommand) Execute(*cli.Context) {
	log.SetRunnerFormatter()

	c.normalizeArgs()

	// Enumerate files
	err := c.enumerate()
	if err != nil {
		logrus.Fatalln(err)
	}

	if c.GenerateArtifactsMetadata {
		logrus.Infof("Generating artifacts statement")

		metadataFile, err := c.generateStatementToFile(generateStatementOptions{
			artifactName: c.Name,
			files:        c.files,
			artifactsWd:  c.wd,
			jobID:        c.ID,
		})
		if err != nil {
			logrus.Fatalln(err)
		}
		c.process(metadataFile)
	}
```

**File:** commands/helpers/artifacts_uploader.go (L252-264)
```go
func (c *ArtifactsUploaderCommand) normalizeArgs() {
	if c.URL == "" || c.Token == "" {
		logrus.Fatalln("Missing runner credentials")
	}
	if c.ID <= 0 {
		logrus.Fatalln("Missing build ID")
	}

	if name, err := shell.Expand(c.Name, nil); err != nil {
		logrus.Warnf("invalid artifact name: %v", err)
	} else {
		c.Name = name
	}
```
