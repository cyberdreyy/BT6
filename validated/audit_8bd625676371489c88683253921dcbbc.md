### Title
Path traversal in `--name` allows artifact metadata file write outside artifacts working directory - ([File: commands/helpers/artifact_metadata.go])

### Summary
`ArtifactsUploaderCommand.normalizeArgs` expands `c.Name` (the `--name` flag, sourced from a job's `artifacts:name` config, e.g. `stages.ArtifactUpload.ArtifactName` / `shells/abstract.go` `writeUploadArtifact`) with `shell.Expand` but performs no path sanitization. That value is then passed unmodified as `artifactName` into `generateStatementToFile`, where it is used directly in `filepath.Join(opts.artifactsWd, fmt.Sprintf(artifactsStatementFormat, opts.artifactName))` to compute the metadata JSON output path, with no containment check against `artifactsWd`.

### Finding Description
- `artifacts:name` in `.gitlab-ci.yml` is fully attacker-controlled (any pipeline author can set it) and flows into `s.ArtifactName` / `artifact.Name`, which is passed via `--name` to the `artifacts-uploader` helper. [1](#0-0) [2](#0-1) 
- `ArtifactsUploaderCommand.normalizeArgs` only shell-expands `c.Name`; it never sanitizes for `..` or path separators. [3](#0-2) 
- The archive body filename path (used for the actual upload multipart filename) *is* sanitized via `artifactFilename`, which calls `filepath.Base(name)` before use. [4](#0-3) 
- However, when `GenerateArtifactsMetadata` is enabled, `Execute` passes the **raw, unsanitized** `c.Name` directly as `artifactName` to `generateStatementToFile`, bypassing the `filepath.Base` sanitization entirely. [5](#0-4) 
- Inside `generateStatementToFile`, the file path is built with `filepath.Join(opts.artifactsWd, fmt.Sprintf(artifactsStatementFormat, opts.artifactName))` and written via `os.WriteFile` with no verification that the resulting path stays within `opts.artifactsWd`. [6](#0-5) 
- `filepath.Join` cleans `..` segments arithmetically but does not prevent escaping the base directory — a name like `../../../tmp/pwn` will produce a path outside `artifactsWd` after cleaning.
- Unlike the file collection path (`fileArchiver.process` / `findRelativePathInProject`), which explicitly checks `strings.HasPrefix(relative, ".."+string(filepath.Separator))` and rejects paths outside the working directory, no equivalent guard exists for the artifact name used in the metadata filename. [7](#0-6) [8](#0-7) 

This confirms the reachable path: `.gitlab-ci.yml` `artifacts:name` → `--name` flag → `ArtifactsUploaderCommand.Name` → `normalizeArgs` (shell.Expand only) → `Execute` → `generateStatementToFile` → `os.WriteFile` at an attacker-influenced path.

### Impact Explanation
An unprivileged pipeline author can set `artifacts:name: "../../../../tmp/pwned"` (or similar) with `GenerateArtifactsMetadata` enabled to make the runner helper process write a JSON file (`<name>-metadata.json`) to an arbitrary filesystem location writable by the job/helper process, outside the job's artifacts working directory. This is a scoped file-write primitive: an attacker can create/overwrite files with attacker-influenced JSON content (containing SLSA provenance data, not fully attacker-controlled bytes, but the file's existence/location and overwrite is attacker-controlled) at any path the runner process user can write to. Depending on executor (shell executor on a shared host, or a container where writable host paths are bind-mounted), this can lead to file overwrite outside the intended job root.

### Likelihood Explanation
- Preconditions: `GenerateArtifactsMetadata` must be enabled (`CI_JOB_GENERATE_ARTIFACTS_METADATA` variable) and format must be zip — this is a documented opt-in feature, but it is a job/pipeline-level toggle a pipeline author can typically control themselves, not an admin-only setting.
- The attacker only needs to control `artifacts:name` in their own `.gitlab-ci.yml`, which is trivial and fully within a normal user's capability.
- The bug is deterministic and repeatable in every run where these conditions hold.

### Recommendation
Sanitize `opts.artifactName` in `generateStatementToFile` (or before calling it, mirroring `artifactFilename`'s `filepath.Base` treatment) and additionally verify the resulting joined path stays within `opts.artifactsWd` (e.g., using `filepath.Rel` and rejecting paths starting with `..`, consistent with the existing containment check pattern in `fileArchiver.findRelativePathInProject`).

### Proof of Concept
```go
func TestGenerateStatementToFile_PathTraversal(t *testing.T) {
    wd := t.TempDir()
    g := &artifactStatementGenerator{
        SLSAProvenanceVersion: "v1",
        StartedAtRFC3339:      time.Now().Format(time.RFC3339),
        EndedAtRFC3339:        time.Now().Format(time.RFC3339),
    }

    file, err := g.generateStatementToFile(generateStatementOptions{
        artifactName: "../../../../tmp/pwn",
        artifactsWd:  wd,
        jobID:        1,
    })
    require.NoError(t, err)

    rel, err := filepath.Rel(wd, file)
    require.NoError(t, err)
    // FAILS today: rel starts with "../", proving the write escaped wd
    assert.False(t, strings.HasPrefix(rel, ".."),
        "metadata file %q escaped artifacts working directory %q", file, wd)
}
```
Expected today: the assertion fails, demonstrating the file is written outside `wd` (e.g., at `/tmp/pwn-metadata.json`), confirming the traversal bug.

### Citations

**File:** functions/concrete/run/stages/artifact_upload.go (L96-98)
```go
	if s.ArtifactName != "" {
		args = append(args, "--name", s.ArtifactName)
	}
```

**File:** shells/abstract.go (L1676-1678)
```go
	if artifact.Name != "" {
		args = append(args, "--name", artifact.Name)
	}
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

**File:** commands/helpers/artifacts_uploader.go (L224-232)
```go
	if c.GenerateArtifactsMetadata {
		logrus.Infof("Generating artifacts statement")

		metadataFile, err := c.generateStatementToFile(generateStatementOptions{
			artifactName: c.Name,
			files:        c.files,
			artifactsWd:  c.wd,
			jobID:        c.ID,
		})
```

**File:** commands/helpers/artifacts_uploader.go (L260-264)
```go
	if name, err := shell.Expand(c.Name, nil); err != nil {
		logrus.Warnf("invalid artifact name: %v", err)
	} else {
		c.Name = name
	}
```

**File:** commands/helpers/artifact_metadata.go (L100-103)
```go
	file := filepath.Join(opts.artifactsWd, fmt.Sprintf(artifactsStatementFormat, opts.artifactName))

	err = os.WriteFile(file, b, 0o644)
	return file, err
```

**File:** commands/helpers/file_archiver.go (L65-88)
```go
func (c *fileArchiver) process(match string) bool {
	var absolute, relative string
	var err error

	absolute, err = filepath.Abs(match)
	if err == nil {
		// Let's try to find a real relative path to an absolute from working directory
		relative, err = filepath.Rel(c.wd, absolute)
	}

	if err == nil {
		// Process path only if it lives in our build directory
		if !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			excluded, rule := c.isExcluded(relative)
			if excluded {
				c.exclude(rule)
				return false
			}

			err = c.add(relative)
		} else {
			err = errors.New("not supported: outside build directory")
		}
	}
```

**File:** commands/helpers/file_archiver.go (L213-216)
```go
	// If fully resolved relative path begins with ".." it is not a subpath of our working directory
	if strings.HasPrefix(rel, ".."+string(filepath.Separator)) || rel == ".." {
		return "", fmt.Errorf("artifact path is not a subpath of project directory: %s", path)
	}
```
