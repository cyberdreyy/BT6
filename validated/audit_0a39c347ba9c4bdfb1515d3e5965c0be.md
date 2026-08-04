Based on my investigation, this is a valid finding, but the root cause is simpler than a strict TOCTOU race: the raw archiver never checks the file type at all, so the "race" is not even necessary — a plain symlink placed before invocation is sufficient. That does not disqualify the finding; it confirms the underlying missing check that the question targets.

### Title
Raw artifact archiver follows symlinks with no type check before `os.Open`, allowing exfiltration of files outside the enumerated/authorized set - ([File: commands/helpers/archive/raw/raw_archiver.go])

### Summary
`(*archiver).Archive` in the raw archiver package opens the pathname captured by `fileArchiver.enumerate()` with a plain `os.Open`, without checking the stored `os.FileInfo`'s mode bits or re-validating the target against the job's working directory. `fileArchiver.add()` uses `os.Lstat` (which does not follow symlinks) to enumerate paths, so a symlink is accepted as a valid artifact entry, and its target is transparently followed when opened later by the raw archiver, unlike the zip/tarzstd archivers, which explicitly special-case `os.ModeSymlink` and write the link target string instead of opening it.

### Finding Description
`fileArchiver.process()`/`add()` restrict the artifact *path string* to be inside `c.wd` via `filepath.Abs`/`filepath.Rel` checks (`commands/helpers/file_archiver.go:69-88`, `191-221`), and `add()` stores whatever `os.Lstat` returns without rejecting `os.ModeSymlink` entries (`commands/helpers/file_archiver.go:127-138`). This means a symlink located inside the job workspace — even one whose target is completely outside the workspace — passes enumeration and is placed in `c.files`.

Later, `ArtifactsUploaderCommand.createBodyProvider` (`commands/helpers/artifacts_uploader.go:112-136`) spins up a goroutine calling `archiver.Archive(context.Background(), c.files)`, and for the raw format that resolves to: [1](#0-0) 
This loop iterates the map key (the pathname string) and calls `os.Open(pathname)` directly — it never inspects `files[pathname]`'s `os.FileInfo` for `os.ModeSymlink`, never calls `filepath.EvalSymlinks`, and never re-validates the resolved path stays under `c.wd`. If `pathname` is (or has been changed into) a symlink, `os.Open` transparently follows it and the target's bytes are streamed into the uploaded artifact body.

Contrast with the other archivers in the same package: `tarzstd_archiver.go` explicitly checks `fi.Mode()&os.ModeSymlink` and uses `os.Readlink` instead of opening the file (`commands/helpers/archive/tarzstd/tarzstd_archiver.go:86-91`), and the zip helpers (`helpers/archives/zip_create.go:68-73`) do the same via `createZipSymlinkEntry`. The raw archiver has no equivalent guard at all, so it is strictly weaker than its siblings.

Because `enumerate()` happens synchronously in `Execute()` before `Run()`/`createBodyProvider()` launches the archiving goroutine (`commands/helpers/artifacts_uploader.go:213-243`), there is a real time window between the `Lstat` in `add()` and the `Open` in `Archive()`. A malicious/compromised job script that controls its own workspace can:
1. Create a regular file at the declared artifact path so enumeration succeeds.
2. Replace it with `ln -sf /path/outside/workspace file` before the archiver goroutine executes (or, even simpler, just declare a symlink to begin with, since `add()`/`process()` never reject `os.ModeSymlink`).
3. `os.Open(pathname)` in `raw_archiver.go` follows the symlink and uploads the target's content as the job artifact.

### Impact Explanation
Any file readable by the process that executes `gitlab-runner-helper artifacts-uploader` (the job's own container/host user in Docker/Kubernetes executors, or the runner process user in shell executor) can be smuggled out through the artifact upload channel as long as the pipeline author sets `--artifact-format raw`. This breaks the invariant that "archived content must match the file that was enumerated/authorized and stay inside the job workspace" — the artifact is no longer confined to files inside `c.wd`.

Note on real-world severity: for the common executors (shell, Docker without special mounts, Kubernetes), the artifacts-uploader helper typically runs with the same filesystem visibility/privilege as the job script itself, so a malicious pipeline author gains little beyond what they could already achieve by reading the file directly in the job script. The bug becomes concretely impactful specifically when the uploader/helper process has filesystem access the job's own script does not (e.g., certain executor configurations where the helper container/process shares mounts or credentials not exposed to the main build container) — that is executor/config dependent and outside pure code review, but the missing check itself is a real, exploitable code defect regardless.

### Likelihood Explanation
Fully feasible and deterministic — no privileged access is required beyond controlling job script content, which is the defined attacker capability. It does not even require a genuine race: simply declaring the artifact path as a symlink before the artifacts-uploader binary runs is sufficient, since `enumerate()`'s `Lstat` doesn't reject symlinks and `raw_archiver.go`'s `Archive` doesn't re-check the type before `os.Open`. The TOCTOU variant described in the question (swap-after-enumerate) is also feasible given the synchronous enumerate-then-async-archive execution flow, but is unnecessary to achieve the impact.

### Recommendation
In `commands/helpers/archive/raw/raw_archiver.go`, before calling `os.Open(pathname)`:
- Re-`os.Lstat` (or use the passed-in `os.FileInfo`) and reject/skip entries with `os.ModeSymlink` or other non-regular mode bits, mirroring the checks already present in `tarzstd_archiver.go` and `helpers/archives/zip_create.go`.
- Additionally/alternatively, resolve the path with `filepath.EvalSymlinks` and verify the result is still prefixed by `c.dir` before opening, to close both the "declared-as-symlink" and "TOCTOU swap" variants.
- Consider adding an equivalent guard to `fileArchiver.add()` so symlinks are rejected/flagged at enumeration time as well, consistent with how other formats treat them.

### Proof of Concept
Go unit test in `commands/helpers/archive/raw` package:
```go
func TestRawArchiver_FollowsSymlinkOutsideWorkspace(t *testing.T) {
    outsideDir := t.TempDir()
    secretPath := filepath.Join(outsideDir, "secret.txt")
    require.NoError(t, os.WriteFile(secretPath, []byte("TOP-SECRET"), 0o600))

    workDir := t.TempDir()
    linkPath := filepath.Join(workDir, "artifact.txt")
    require.NoError(t, os.Symlink(secretPath, linkPath))

    fi, err := os.Lstat(linkPath) // mirrors fileArchiver.add()
    require.NoError(t, err)

    a, err := NewArchiver(&buf, workDir, archive.DefaultCompression)
    require.NoError(t, err)

    err = a.Archive(context.Background(), map[string]os.FileInfo{linkPath: fi})
    require.NoError(t, err)

    // Assert: the uploaded/archived content should NOT equal the outside file's
    // content, since the symlink target lies outside the job workspace.
    assert.NotEqual(t, "TOP-SECRET", buf.String(),
        "raw archiver followed a symlink outside the job workspace")
}
```
A TOCTOU-focused race test would additionally spawn a goroutine that atomically renames a regular file to a symlink pointing outside `c.wd` concurrently with `Archive()`, asserting across many iterations that the archived bytes are never sourced from outside `workDir`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** commands/helpers/archive/raw/raw_archiver.go (L35-49)
```go
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	if len(files) > 1 {
		return ErrTooManyRawFiles
	}

	for pathname := range files {
		f, err := os.Open(pathname)
		if err != nil {
			return err
		}
		defer f.Close()

		_, err = io.Copy(a.w, f)
		return err
	}
```

**File:** commands/helpers/file_archiver.go (L127-138)
```go
func (c *fileArchiver) add(path string) error {
	// Always use slashes
	path = filepath.ToSlash(path)

	// Check if file exist
	info, err := os.Lstat(path)
	if err == nil {
		c.files[path] = info
	}

	return err
}
```

**File:** commands/helpers/file_archiver.go (L264-282)
```go
func (c *fileArchiver) enumerate() error {
	wd, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current working directory: %w", err)
	}

	c.wd = wd
	c.files = make(map[string]os.FileInfo)
	c.excluded = make(map[string]int64)

	c.processPaths()
	c.processUntracked()

	for path, count := range c.excluded {
		logrus.Infof("%s: excluded %d files", path, count)
	}

	return nil
}
```

**File:** commands/helpers/artifacts_uploader.go (L112-136)
```go
	streamProvider := common.StreamProvider{
		ReaderFactory: func() (io.ReadCloser, error) {
			pr, pw := io.Pipe()

			archiver, archiveErr := archive.NewArchiver(archive.Format(format), pw, c.wd, GetCompressionLevel(c.CompressionLevel))
			if archiveErr != nil {
				pr.CloseWithError(archiveErr)
				return nil, archiveErr
			}

			// Start a new Goroutine to create the archive for this attempt
			go func() {
				archiveErr := archiver.Archive(context.Background(), c.files)
				pw.CloseWithError(archiveErr)
			}()

			meteredReader := meter.NewReader(
				pr,
				c.TransferMeterFrequency,
				meter.LabelledRateFormat(os.Stdout, "Uploading artifacts", meter.UnknownTotalSize),
			)

			return meteredReader, nil
		},
	}
```

**File:** commands/helpers/artifacts_uploader.go (L213-243)
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

	// If the upload fails, exit with a non-zero exit code to indicate an issue?
	if err := retry.WithFn(c, c.Run).Run(); err != nil {
		logrus.Fatalln(err)
	}
}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_archiver.go (L85-91)
```go
		var link string
		if fi.Mode()&os.ModeSymlink != 0 {
			link, err = os.Readlink(path)
			if err != nil {
				return err
			}
		}
```

**File:** helpers/archives/zip_create.go (L17-30)
```go
func createZipSymlinkEntry(archive *zip.Writer, fh *zip.FileHeader) error {
	fw, err := archive.CreateHeader(fh)
	if err != nil {
		return err
	}

	link, err := os.Readlink(fh.Name)
	if err != nil {
		return err
	}

	_, err = io.WriteString(fw, link)
	return err
}
```
