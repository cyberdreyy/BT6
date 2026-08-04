### Title
Zip-slip path traversal in `extractZipFile`/`extractZipDirectoryEntry` allows writing outside extraction root - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipDirectoryEntry`, `extractZipSymlinkEntry`, and `extractZipFileEntry` in `helpers/archives/zip_extract.go` use `file.Name` from the zip archive directly, with no validation against path traversal (`..`), absolute paths, or symlink redirection outside the extraction root. The only safety check applied, `errorIfGitDirectory`, only rejects `.git` paths and does nothing for zip-slip style traversal.

### Finding Description
`ExtractZipArchive` [1](#0-0)  iterates archive entries and calls `extractZipFile` for each, which does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` followed by dispatch to `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, or `extractZipFileEntry` [2](#0-1) . None of these functions call `filepath.Clean`, check for a leading `/` (absolute path) or `..` path segments, or verify the resolved path stays within the intended destination directory before performing `os.Mkdir`, `os.Symlink`, or `os.OpenFile` [3](#0-2) .

The single content check present, `errorIfGitDirectory`/`isPathAGitDirectory` in `helpers/archives/path_check_helper.go`, only detects a `.git` first path segment; it does not detect or block `../` traversal or absolute paths [4](#0-3) . Errors from `extractZipFile` are only logged via a deduplicating `pathErrorTracker` and do not abort extraction of the remaining entries [5](#0-4) .

This code path is reachable from `ArtifactsDownloaderCommand.Execute`, which downloads a job's dependency artifacts (fully attacker-controlled content for cross-job/dependency artifact consumption) and calls into the extractor abstraction that ultimately uses zip extraction [6](#0-5) , and from the ziplegacy extractor (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) as well as cache extraction paths noted in `cache_extractor_test.go`.

However, I was not able to fully confirm within available context whether the default/production extraction path for artifacts and cache actually routes through this legacy zip code (`helpers/archives/zip_extract.go`) versus the `fastzip` extractor (`commands/helpers/archive/fastzip/zip_fastzip_archiver.go`), which may implement its own path-safety checks (a common feature of the `fastzip` library). The `ziplegacy` package clearly wraps this code, but I could not verify from the index which extractor is selected by default versus as a fallback, nor examine the `fastzip` archiver's extraction logic for equivalent protections. This uncertainty should be resolved by inspecting `commands/helpers/archive/fastzip/zip_fastzip_archiver.go` and `commands/helpers/archive/archive.go` (extractor selection logic) directly.

### Impact Explanation
If reachable without the `fastzip` mitigation applying, an attacker who controls an artifact or cache archive consumed by a later job/stage (e.g., a dependency artifact from an earlier job in the same pipeline, or a shared cache) can craft zip entries with `../` sequences or symlink entries pointing outside the extraction root. This could overwrite files in the build directory tree that later runner-generated stages implicitly execute or source (e.g., generated shell scripts, `after_script`, custom executor hook files), leading to code execution in a later job's build context or exposure of variables/secrets processed by that later stage.

### Likelihood Explanation
Preconditions: the job must consume an attacker-influenced artifact or cache archive (e.g., `needs`/`dependencies` from an attacker-controlled earlier job, or a cache key collision), and the effective extraction implementation used at runtime must be the legacy zip path lacking sanitization rather than a fastzip path with built-in protection. If the legacy path is used (confirmed to lack protections here), the exploit is straightforward and deterministic — a normal pipeline author can craft the archive with standard zip-with-traversal-name tooling. The overall likelihood is uncertain pending confirmation of which extractor backend is used by default in this build.

### Recommendation
Sanitize every `file.Name` before use in `extractZipFile`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry`/`extractZipFileEntry`: reject or `filepath.Clean` and verify (via `filepath.Rel` from the destination root, checking for no leading `..` or absolute path) that the resolved target path stays within the intended extraction root, mirroring standard zip-slip mitigations. Apply the same containment check to symlink targets (`extractZipSymlinkEntry`) so a symlink cannot be created pointing outside the root, and to symlink target resolution before writing through it in later extraction passes (`lchmod`/`processZipExtra`).

### Proof of Concept
Go unit test in `helpers/archives/zip_extract_test.go` style:
1. Build an in-memory zip (`archive/zip.Writer`) with an entry named `../../evil.sh` (or on Windows equivalent) containing attacker payload.
2. Call `ExtractZipArchive` with the destination set to a temp subdirectory `dst`.
3. Assert: `os.Stat(filepath.Join(filepath.Dir(dst), "evil.sh"))` does NOT exist (currently it likely does, proving the bug), i.e. assert extraction failed/was rejected rather than writing outside `dst`.
4. Additionally craft a symlink entry (`os.ModeSymlink`) named `link` whose target is `/etc/passwd` or an absolute path outside root, and assert `extractZipSymlinkEntry` refuses to create it.

Whether this constitutes a currently exploitable production bug depends on confirming (via further code reading of `commands/helpers/archive/archive.go` and `fastzip/zip_fastzip_archiver.go`) that this unsanitized legacy path is actually reachable for artifact/cache extraction in the default runner configuration rather than being superseded by a safer `fastzip` implementation.

### Citations

**File:** helpers/archives/zip_extract.go (L12-59)
```go
func extractZipDirectoryEntry(file *zip.File) (err error) {
	err = os.Mkdir(file.Name, file.Mode().Perm())

	// The "directory does exist" error is not an error for us
	if os.IsExist(err) {
		err = nil
	}
	return
}

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

**File:** helpers/archives/path_check_helper.go (L13-19)
```go
func isPathAGitDirectory(path string) bool {
	parts := strings.Split(filepath.Clean(path), string(filepath.Separator))
	if len(parts) > 0 && parts[0] == ".git" {
		return true
	}
	return false
}
```

**File:** commands/helpers/artifacts_downloader.go (L125-140)
```go
	f, size, format, err := openArchive(file.Name())
	if err != nil {
		logrus.Fatalln(err)
	}
	defer f.Close()

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	// Extract artifacts file
	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```
