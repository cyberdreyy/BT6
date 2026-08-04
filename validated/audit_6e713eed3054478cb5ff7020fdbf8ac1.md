### Title
Unsanitized artifact name allows path traversal write in build-provenance metadata generation - (File: commands/helpers/artifact_metadata.go)

### Summary
`generateStatementToFile` builds the metadata output path by directly `fmt.Sprintf`-ing the raw, attacker-controlled `opts.artifactName` into a filename and joining it with `opts.artifactsWd`, then calling `os.WriteFile`. Unlike the actual archive filename (which is sanitized with `filepath.Base` in `artifactFilename`), the metadata filename path receives no such sanitization, so a crafted artifact name containing `../` sequences can make the resulting `file` path resolve outside the job working directory.

### Finding Description
In `ArtifactsUploaderCommand.Execute` (`commands/helpers/artifacts_uploader.go:213-243`), `c.Name` is only shell-expanded via `normalizeArgs` (`shell.Expand(c.Name, nil)`, line 260-264) — no path cleaning, no `filepath.Base`, no character allow-listing. That raw value is passed straight through as `artifactName` into `generateStatementOptions` (line 228) when `GenerateArtifactsMetadata` is set.

Compare this to `createBodyProvider`/`artifactFilename` (`commands/helpers/artifacts_uploader.go:79-96`), which explicitly calls `filepath.Base(name)` before using the name as the archive filename — a mitigation that exists for the *archive* name but was not applied to the *metadata* filename path.

In `generateStatementToFile` (`commands/helpers/artifact_metadata.go:100-102`):
```go
file := filepath.Join(opts.artifactsWd, fmt.Sprintf(artifactsStatementFormat, opts.artifactName))
err = os.WriteFile(file, b, 0o644)
```
`filepath.Join` calls `filepath.Clean` on the joined path, so `../` segments in `artifactName` are honored and can walk the resulting path outside `opts.artifactsWd` (which is `c.wd`, the job's build directory). Because the format string is `"%v-metadata.json"`, an attacker only needs to control the base of the name (e.g. `artifactName = "../../../../tmp/evil"`), producing a `file` value like `/tmp/evil-metadata.json` outside the intended artifacts working directory, followed by an unconditional `os.WriteFile` with attacker-uncontrolled (but runner-generated) content.

`artifactName` originates from `ArtifactUpload.ArtifactName` (`functions/concrete/run/stages/artifact_upload.go:16,96-98`), which is passed as `--name` to the `artifacts-uploader` helper binary. This value is derived from the job's `artifacts:name` configuration in `.gitlab-ci.yml`, which is fully controlled by whoever authors the pipeline definition — an unprivileged pipeline author, matching the stated attacker model. Since `GenerateArtifactsMetadata`/`Metadata` is populated whenever build provenance/attestation is enabled for the job (server/project-level toggle, unrelated to the attacker's ability to set `artifacts:name`), the write path is reachable purely with attacker-controlled `artifact:name` content once provenance generation is active for a project.

No existing check intervenes: `normalizeArgs` only performs shell variable expansion, and `generateStatementToFile` performs no validation of `opts.artifactName` before building the file path.

### Impact Explanation
An unprivileged pipeline author can, via a crafted `artifacts:name` value with `../` traversal sequences, cause the runner helper process to write the generated in-toto/SLSA metadata JSON file to an arbitrary path outside the job's `CI_PROJECT_DIR`/artifacts working directory on the runner/helper filesystem, subject to the process's filesystem permissions. This is an unauthorized file write outside the job root, matching the scoped impact (the content is runner-generated attestation JSON, not attacker-controlled bytes, but the write location is attacker-controlled).

### Likelihood Explanation
Preconditions: (1) the project/job must have build provenance / artifact metadata generation enabled (`--generate-artifacts-metadata`), which is an existing feature toggle unrelated to attacker privilege; (2) the attacker needs only edit access to `.gitlab-ci.yml` to set a crafted `artifacts:name`. Both preconditions are realistic for a normal pipeline author and require no elevated runner or admin access. The bug is deterministic and repeatable — every job run with the crafted name reproduces the traversal.

### Recommendation
Sanitize `opts.artifactName` in `generateStatementToFile` the same way `artifactFilename` sanitizes the archive name — e.g. apply `filepath.Base(opts.artifactName)` (and reject/replace empty or `.`/`..` results) before formatting the metadata filename, or explicitly validate that the resulting `file` path (after `filepath.Clean`) remains within `opts.artifactsWd` (e.g., via `filepath.Rel` and rejecting results starting with `..`) before calling `os.WriteFile`.

### Proof of Concept
Add to `commands/helpers/artifact_metadata_test.go`:
```go
func TestGenerateMetadataToFile_PathTraversal(t *testing.T) {
	tmpDir := t.TempDir()
	outsideDir := t.TempDir() // simulates location outside job root

	g := &artifactStatementGenerator{
		StartedAtRFC3339:      time.Now().Format(time.RFC3339),
		EndedAtRFC3339:        time.Now().Format(time.RFC3339),
		SLSAProvenanceVersion: slsaProvenanceVersion1,
	}

	// craft a name that escapes tmpDir into outsideDir
	rel, err := filepath.Rel(tmpDir, outsideDir)
	require.NoError(t, err)
	maliciousName := filepath.Join(rel, "evil")

	f, err := g.generateStatementToFile(generateStatementOptions{
		artifactName: maliciousName,
		files:        map[string]os.FileInfo{},
		artifactsWd:  tmpDir,
		jobID:        1,
	})
	require.NoError(t, err)

	// Assert the written file escaped tmpDir
	relResult, err := filepath.Rel(tmpDir, f)
	require.NoError(t, err)
	assert.True(t, strings.HasPrefix(relResult, ".."), "expected file to escape artifactsWd, got %s", f)

	_, statErr := os.Stat(f)
	assert.NoError(t, statErr, "file was written outside artifactsWd")
}
```
Expected result on the current code: the test passes, proving `f` resolves outside `tmpDir` and a file is written there — confirming the path-traversal write. After applying the recommended sanitization, the test should fail to escape (fix would need `filepath.Base`/containment check, which would need the test updated accordingly to assert containment instead). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** commands/helpers/artifact_metadata.go (L100-103)
```go
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

**File:** commands/helpers/artifacts_uploader.go (L224-237)
```go
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

**File:** functions/concrete/run/stages/artifact_upload.go (L90-98)
```go
	if s.Metadata != nil {
		args = append(args, s.Metadata.args()...)
	}

	args = append(args, archiverArgs...)

	if s.ArtifactName != "" {
		args = append(args, "--name", s.ArtifactName)
	}
```
