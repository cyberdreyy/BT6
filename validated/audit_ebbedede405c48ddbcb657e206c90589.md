### Title
Zip artifact extraction lacks path traversal / zip-slip protection, allowing writes outside job working directory - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFile` (and its helpers `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, `extractZipFileEntry`) use `file.Name` from the zip archive directly, with no validation against `..` traversal or absolute paths, and no containment against a target root. Because the `dir` parameter passed into `ziplegacy.extractor.Extract` is never used when calling `archives.ExtractZipArchive(zr)`, the extraction target is simply the process's current working directory, which for `ArtifactsDownloaderCommand.Execute` is the downloading job's `wd`.

### Finding Description
The call chain is: `commands/helpers/artifacts_downloader.go` `ArtifactsDownloaderCommand.Execute` → `openArchive` → `archive.NewExtractor(archive.Zip, f, size, wd)` → registered zip extractor. For the legacy zip path (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`), `NewExtractor` stores `dir` in the `extractor` struct [1](#0-0) , but `Extract` never uses `e.dir`; it calls `archives.ExtractZipArchive(zr)` directly [2](#0-1) .

`ExtractZipArchive` iterates zip entries and calls `extractZipFile(file)` for each, using `file.Name` verbatim [3](#0-2) . `extractZipFile` calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then dispatches to directory/symlink/file writers, none of which sanitize `file.Name` for `..` segments or absolute paths [4](#0-3) . `extractZipFileEntry` performs `os.Remove(file.Name)` then `os.OpenFile(file.Name, ...)` with the raw, attacker-controlled name [5](#0-4) . `extractZipSymlinkEntry` similarly creates a symlink at an unsanitized path pointing to attacker-controlled zip content [6](#0-5) .

Since no containment check exists and the effective extraction root is simply the process's cwd (which equals the *downloading* job's `wd`, set via `os.Getwd()` in `ArtifactsDownloaderCommand.Execute` [7](#0-6) ), a zip entry named e.g. `../../../../home/victim/secret` written by an attacker in Project A, once fetched as an artifact by a job in Project B (via cross-project/`needs:project` artifact consumption, a documented GitLab feature), extracts relative to Project B's job working directory tree, escaping it entirely. Existing protections — `errorIfGitDirectory` (only warns about `.git` directory overwrite, doesn't block traversal) and `pathErrorTracker` (only deduplicates log spam) — do not prevent or detect path escape [3](#0-2) .

### Impact Explanation
An unprivileged attacker who can produce/upload a job artifact (their own pipeline) can craft a zip with `../`-prefixed entry names or symlink entries. When that artifact is subsequently downloaded and extracted by a *different* project/job's `gitlab-runner-helper artifacts-downloader` invocation (cross-project artifact consumption), files/symlinks are written outside the intended `wd`, anywhere the extracting process's OS-level file permissions allow. This can overwrite or plant files in the victim job's checkout, poison files outside the checkout on shared executors, or (via symlink entries evaluated later by other extraction/read logic) exfiltrate victim data. Concrete outcomes: victim's project files overwritten, malicious executables planted for later job steps, or files written outside the whole build directory tree on shared-executor setups.

### Likelihood Explanation
Feasible and repeatable: an attacker only needs the ability to author a CI job whose artifact zip is later consumed by another project's job (a supported GitLab feature: `needs:project`, cross-pipeline/downstream artifact fetching, or generic artifact download of an accessible job). No special runner privilege is required — this is purely a client-controlled zip content issue reachable through the normal artifact download path used by every job. It is deterministic: the same malicious zip always writes to the same relative traversal path regardless of which job/project decompresses it.

### Recommendation
In `helpers/archives/zip_extract.go`, before processing each `zip.File`, resolve `file.Name` against the intended extraction root (`dir`) using `filepath.Join` + verification that the resulting cleaned absolute path has the root as a prefix (the standard zip-slip fix), rejecting entries that escape (also validating symlink targets similarly). Additionally, thread the `dir` parameter from `ziplegacy.extractor.Extract` into `archives.ExtractZipArchive`/`extractZipFile` instead of relying on implicit CWD, so extraction is always anchored to the caller-provided directory and can be defensively validated (mirroring the fastzip extractor which uses `fastzip.NewExtractorFromReader(..., e.dir, ...)`, and should be confirmed to itself enforce containment).

### Proof of Concept
Go unit test in `helpers/archives`:
```go
func TestExtractZipFile_PathTraversalBlocked(t *testing.T) {
    tmpRoot := t.TempDir()
    victimOutside := filepath.Join(filepath.Dir(tmpRoot), "victim-outside.txt")
    defer os.Remove(victimOutside)

    // build in-memory zip with a traversal entry
    buf := &bytes.Buffer{}
    zw := zip.NewWriter(buf)
    fw, _ := zw.Create("../victim-outside.txt")
    fw.Write([]byte("pwned"))
    zw.Close()

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))

    chdir(t, tmpRoot) // simulate victim job's wd
    err := archives.ExtractZipArchive(zr) // should fail or be a no-op for the traversal entry
    require.NoError(t, err)

    _, statErr := os.Stat(victimOutside)
    assert.True(t, os.IsNotExist(statErr), "traversal entry must not be written outside extraction root")
}
```
Expected result with current code: `victim-outside.txt` is created outside `tmpRoot`, proving the bug; after the fix, the assertion that the file does not exist outside the root should pass.

### Citations

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L19-22)
```go
// NewExtractor returns a new Zip Extractor.
func NewExtractor(r io.ReaderAt, size int64, dir string) (archive.Extractor, error) {
	return &extractor{r: r, size: size, dir: dir}, nil
}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L24-32)
```go
// Extract extracts files from the reader to the directory passed to
// NewZipExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
```

**File:** helpers/archives/zip_extract.go (L22-39)
```go
func extractZipSymlinkEntry(file *zip.File) (err error) {
	var data []byte
	in, err := file.Open()
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	data, err = io.ReadAll(in)
	if err != nil {
		return err
	}

	// Remove symlink before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	err = os.Symlink(string(data), file.Name)
	return
}
```

**File:** helpers/archives/zip_extract.go (L41-59)
```go
func extractZipFileEntry(file *zip.File) (err error) {
	var out *os.File
	in, err := file.Open()
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	// Remove file before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	out, err = os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, file.Mode().Perm())
	if err != nil {
		return err
	}
	defer func() { _ = out.Close() }()
	_, err = io.Copy(out, in)

	return
}
```

**File:** helpers/archives/zip_extract.go (L61-83)
```go
func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
	}

	switch file.Mode() & os.ModeType {
	case os.ModeDir:
		err = extractZipDirectoryEntry(file)

	case os.ModeSymlink:
		err = extractZipSymlinkEntry(file)

	case os.ModeNamedPipe, os.ModeSocket, os.ModeDevice:
		// Ignore files of these types
		logrus.Warningf("File ignored: %q", file.Name)

	default:
		err = extractZipFileEntry(file)
	}
	return
}
```

**File:** helpers/archives/zip_extract.go (L85-96)
```go
func ExtractZipArchive(archive *zip.Reader) error {
	tracker := newPathErrorTracker()

	for _, file := range archive.File {
		if err := errorIfGitDirectory(file.Name); tracker.actionable(err) {
			printGitArchiveWarning("extract")
		}

		if err := extractZipFile(file); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}
```

**File:** commands/helpers/artifacts_downloader.go (L88-94)
```go
func (c *ArtifactsDownloaderCommand) Execute(cliContext *cli.Context) {
	log.SetRunnerFormatter()

	wd, err := os.Getwd()
	if err != nil {
		logrus.Fatalln("Unable to get working directory")
	}
```
