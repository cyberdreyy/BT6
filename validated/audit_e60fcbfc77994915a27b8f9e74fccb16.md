### Title
Zip Slip / Path Traversal via Unsanitized Archive Entry Names in Legacy Zip Extractor - (File: `helpers/archives/zip_extract.go`)

### Summary
GitLab Runner's legacy zip archive extractor writes files to disk using the raw `file.Name` field taken directly from the zip archive's central directory, without validating that the resulting path stays within the intended extraction directory. This is the same root-cause pattern as the referenced Augur report ("lack of input validation on user-controlled parameters"): an externally-supplied, attacker-influenced value (`zip.File.Name`) is trusted and used directly in a filesystem write operation. This contrasts with the sibling `tarzstd` extractor, which explicitly validates the resolved path against the target directory before writing.

### Finding Description
`extractZipFile`/`extractZipFileEntry`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry` in [1](#0-0)  operate directly on `file.Name` from the `archive/zip.File` struct with no containment check:
- `extractZipDirectoryEntry` calls `os.Mkdir(file.Name, ...)` [2](#0-1) 
- `extractZipSymlinkEntry` calls `os.Symlink(string(data), file.Name)`, i.e., an attacker also fully controls the symlink target contents [3](#0-2) 
- `extractZipFileEntry` calls `os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)` [4](#0-3) 
- `extractZipFile` creates parent directories with `os.MkdirAll(filepath.Dir(file.Name), 0o777)` before dispatching to the entry handlers [5](#0-4) 

None of these functions call `filepath.Abs`/`filepath.Clean` and compare the resolved path against the extraction root, so a crafted entry name such as `../../../../etc/cron.d/x` or an absolute path is followed verbatim (this is the classic "Zip Slip" vulnerability). `ExtractZipArchive` (the caller) only checks for `.git` directory names for a warning — it performs no path-containment validation at all [6](#0-5) .

This is invoked by `commands/helpers/cache_extractor.go` and `commands/helpers/artifacts_downloader.go` via the `ziplegacy` package's `NewExtractor` [7](#0-6) , which is one of the selectable zip extraction backends alongside `fastzip`.

By contrast, the project's own `tarzstd` extractor demonstrates that the maintainers are aware of this exact input-validation requirement and implement it correctly there: it resolves the joined path with `filepath.Abs` and explicitly rejects any path that escapes the target directory ("cannot be extracted outside of chroot") [8](#0-7) . The `zip_extract.go` code path lacks the equivalent check, which is the missing-input-validation gap analogous to the referenced report's pattern (an unsanitized, user/attacker-controlled field flowing unchecked into a sensitive operation).

### Impact Explanation
When this extractor is used to unpack a cache or artifacts zip (job artifacts and caches are attacker-influenceable content — a CI job author fully controls what gets zipped and uploaded as their job's artifacts/cache), a crafted archive can:
- Write or overwrite arbitrary files outside the designated build/cache directory on the runner host (path traversal via `../` sequences or absolute paths in `file.Name`).
- Create symlinks pointing anywhere on the filesystem via `extractZipSymlinkEntry`, since both the symlink path and its target content are attacker-controlled.

On a runner host where the extraction happens in a shared filesystem context (e.g., shell executor, or any executor where the extraction step runs with meaningful host filesystem access), this can lead to overwriting configuration files, planting malicious files in paths later executed, or corrupting other jobs' data — i.e., unauthorized state changes / persistent corruption outside the intended sandbox boundary, consistent with a "restriction bypass" vulnerability class.

### Likelihood Explanation
The attacker precondition is only "can define/control a CI job's artifact or cache contents," which is a normal, unprivileged CI user capability (no admin/operator access, no leaked secrets required) — this matches the "malicious normal user abusing valid product flows" attacker profile. The likelihood depends on: (1) the `ziplegacy` backend actually being selected for a given extraction (vs. `fastzip`, which uses a hardened third-party library) — I was not able to fully confirm at which conditions `ziplegacy` vs `fastzip` is chosen at runtime within the scope of this investigation, and (2) whether the destination filesystem context gives the traversal any meaningful blast radius (e.g., isolated per-job containers with ephemeral filesystems reduce impact, whereas shared/persistent build directories increase it). This uncertainty should be resolved by a maintainer/deeper trace of the extractor-selection logic before treating severity as definitive.

### Recommendation
Add explicit path-containment validation in `helpers/archives/zip_extract.go`, mirroring the check already present in `commands/helpers/archive/tarzstd/tarzstd_extractor.go`:
- Resolve each `file.Name` with `filepath.Join(destDir, file.Name)` then `filepath.Abs`/`filepath.Clean`.
- Reject (with a clear, user-facing error) any entry whose resolved path does not have `destDir` as a prefix.
- Apply the same validation to symlink targets (`extractZipSymlinkEntry`) to prevent symlinks from being created outside the destination directory or pointing outside it in a way that is later dereferenced destructively.
- Ensure this validation runs before any `os.Mkdir`, `os.MkdirAll`, `os.OpenFile`, or `os.Symlink` call, consistent with the require-early pattern recommended in the referenced report.

### Proof of Concept
1. Craft a zip archive containing an entry with `Name` set to `../../../malicious.txt` (or, on extraction to a build directory `/builds/proj/`, effectively targeting a path outside it), using Go's `archive/zip` writer directly (bypassing GitLab Runner's own archiver, since the vulnerability is in the extractor, not the archiver).
2. Upload this archive as a job artifact / cache from a normal (unprivileged) CI job.
3. Trigger a subsequent job/step that downloads and extracts this artifact/cache using the `ziplegacy` backend (`commands/helpers/artifacts_downloader.go` / `commands/helpers/cache_extractor.go`).
4. Observe that `extractZipFile` in `helpers/archives/zip_extract.go` writes `malicious.txt` outside the intended extraction directory, since no containment check exists [5](#0-4) , in contrast to the tarzstd extractor's chroot check which would reject the equivalent case [8](#0-7) .

**Note on scope/limitation:** I could not fully verify within this session the exact runtime conditions under which the `ziplegacy` backend (vs. `fastzip`) is selected for extraction; a Devin session with full repo/build access would be needed to trace `commands/helpers/archiver.go`'s selection logic end-to-end and confirm real-world reachability precisely.

### Citations

**File:** helpers/archives/zip_extract.go (L12-83)
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

**File:** helpers/archives/zip_extract.go (L85-110)
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

	for _, file := range archive.File {
		if err := lchmod(file.Name, file.Mode()); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}

		// Process zip metadata
		if err := processZipExtra(&file.FileHeader); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}

	return nil
}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L19-35)
```go
// NewExtractor returns a new Zip Extractor.
func NewExtractor(r io.ReaderAt, size int64, dir string) (archive.Extractor, error) {
	return &extractor{r: r, size: size, dir: dir}, nil
}

// Extract extracts files from the reader to the directory passed to
// NewZipExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
}


```

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L57-64)
```go
		var path string
		path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
			return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
		}
```
