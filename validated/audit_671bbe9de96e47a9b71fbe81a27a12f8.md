### Title
Path traversal in artifact metadata filename via `artifacts:name` - ([File: commands/helpers/artifact_metadata.go])

### Summary
`artifactStatementGenerator.generateStatementToFile` builds the metadata output path with `filepath.Join(opts.artifactsWd, fmt.Sprintf(artifactsStatementFormat, opts.artifactName))`, where `opts.artifactName` is `ArtifactsUploaderCommand.Name`, sourced directly from the job's `artifacts:name` configuration with no sanitization applied before this call. This differs from the sibling `artifactFilename` function which explicitly calls `filepath.Base(name)` before using the name for the archive file, showing the sanitization was intentionally applied there but omitted here.

### Finding Description
The call chain is: `ArtifactsUploaderCommand.Execute` → `generateStatementToFile(generateStatementOptions{artifactName: c.Name, ...})` [1](#0-0)  → `filepath.Join(opts.artifactsWd, fmt.Sprintf(artifactsStatementFormat, opts.artifactName))` → `os.WriteFile(file, b, 0o644)` [2](#0-1) .

`c.Name` only goes through `shell.Expand` in `normalizeArgs`, which expands shell variable syntax but performs no path sanitization or `..` stripping [3](#0-2) . By contrast, the archive-filename path (`createBodyProvider` → `artifactFilename`) explicitly calls `filepath.Base(name)` to strip any directory components before using the name in a file path [4](#0-3) . No equivalent guard exists for the metadata-statement path. `filepath.Join` calls `filepath.Clean`, which resolves `..` segments; if `opts.artifactName` contains a value like `../../evil`, the resulting `Sprintf("%v-metadata.json", "../../evil")` becomes `../../evil-metadata.json`, and `filepath.Join(artifactsWd, ...)` can resolve to a location outside `artifactsWd`, and `os.WriteFile` will write there.

### Impact Explanation
A pipeline author can set `artifacts:name: "../../evil"` and enable artifact metadata generation (`GenerateArtifactsMetadata`/`--generate-artifacts-metadata`, which is used when SLSA provenance/attestation generation is enabled for artifacts). This results in `os.WriteFile` writing a JSON file to a directory outside the intended artifacts working directory, i.e., an out-of-scope file write within whatever filesystem the artifacts-uploader helper process has access to (the job's execution environment). This matches the scoped impact: unauthorized file write outside the job artifacts working directory.

### Likelihood Explanation
Preconditions are simple and fully attacker-controlled: the job only needs to set `artifacts:name` to a traversal string and have artifact metadata/attestation generation enabled. No additional privilege beyond normal pipeline authorship is required, and the bug is deterministically reachable every time this code path executes with such a name — it is not a race condition or environment-dependent issue.

### Recommendation
Sanitize `opts.artifactName` the same way `artifactFilename` sanitizes `c.Name` — apply `filepath.Base` (and reject/replace empty or `.`/`..` results) before formatting it into the metadata filename in `generateStatementToFile`, or validate that the resulting joined path remains within `opts.artifactsWd` (e.g., via `filepath.Rel` and checking for a leading `..`) before calling `os.WriteFile`.

### Proof of Concept
Add a Go unit test in `commands/helpers/artifact_metadata_test.go` similar to the existing `TestGenerateMetadataToFile` table but with:
```go
"path traversal in artifact name": {
    newGenerator: newGenerator,
    opts: generateStatementOptions{
        artifactName: "../../evil",
        files:        map[string]os.FileInfo{tmpFile.Name(): fileInfo{name: tmpFile.Name()}},
        artifactsWd:  tmpDir,
        jobID:        1000,
    },
},
```
After calling `f, err := g.generateStatementToFile(tt.opts)`, assert:
```go
rel, relErr := filepath.Rel(tmpDir, f)
require.NoError(t, relErr)
assert.False(t, strings.HasPrefix(rel, ".."), "resolved metadata file escaped artifactsWd: %s", f)
```
With the current implementation this assertion fails, confirming the file is written outside `tmpDir`.

### Citations

**File:** commands/helpers/artifacts_uploader.go (L79-83)
```go
func (c *ArtifactsUploaderCommand) artifactFilename(name string, format spec.ArtifactFormat) string {
	name = filepath.Base(name)
	if name == "" || name == "." {
		name = DefaultUploadName
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
