### Title
Zip archive extraction allows path traversal combined with `os.Lchown`/`lchmod` on paths outside extraction root - ([File: helpers/archives/zip_extract.go])

### Summary
`ExtractZipArchive` writes and then `lchmod`/`os.Lchown`s every zip entry using the raw, attacker-controlled `file.Name` from the archive with no path-containment check. A crafted `Name` containing `../` traversal sequences lets a job's zip (used for caches/artifacts) create/modify a file outside the intended extraction directory and then change its ownership (via a `ZipUIDGidField` extra field) and permission bits, entirely under attacker control.

### Finding Description
`extractZipFile` in `helpers/archives/zip_extract.go` operates directly on `file.Name` without any path sanitization: it does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then `os.OpenFile(file.Name, ...)` / `os.Symlink(...)` / `os.Mkdir(file.Name, ...)` depending on entry type. [1](#0-0) 

The only pre-extraction check performed is `errorIfGitDirectory`, which only warns about `.git` directories and does nothing to prevent path traversal or absolute paths. [2](#0-1) 

After all files are written, `ExtractZipArchive` runs a **second loop** over the same `file.Name` values, calling `lchmod(file.Name, file.Mode())` and `processZipExtra`, which dispatches to `processZipUIDGidField` when a `ZipUIDGidFieldType` extra field is present. [3](#0-2) 

`processZipUIDGidField` parses the attacker-supplied UID/GID extra field and calls `os.Lchown(file.Name, int(ugField.UID), int(ugField.Gid))` directly on the same unsanitized name. [4](#0-3) 

`lchmod` similarly applies the mode bits from the zip header directly to `file.Name` via `unix.Fchmodat` with `AT_SYMLINK_NOFOLLOW`. [5](#0-4) 

Because `file.Name` is never validated to stay within the extraction root (no `filepath.Clean`/`filepath.Rel` containment check, no rejection of `..` segments or absolute paths), a zip entry named e.g. `../../../some/other/path` will:
1. Be written/created outside the extraction directory (zip-slip write).
2. Have its **mode bits** changed via `lchmod` to whatever the attacker specifies in the zip header.
3. Have its **ownership** changed via `os.Lchown` to an arbitrary UID/GID if a `ZipUIDGidField` extra field is included.

The extraction entry point (`ExtractZipFile`/`ExtractZipArchive`) is reachable from the legacy zip extractor used for cache and artifact extraction, `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`, which simply forwards to `archives.ExtractZipArchive(zr)` without any additional path validation. [6](#0-5) 

This extractor is invoked by `CacheExtractorCommand.Execute` and `ArtifactsDownloaderCommand.Execute`, both of which extract into the process's current working directory (the job's build directory) with no chroot/jail boundary enforced at the Go level. [7](#0-6) [8](#0-7) 

The archive content (cache/artifact zip file, `Name` field, and `Extra` field bytes including `ZipUIDGidField`) is entirely attacker-controlled: a job can produce and upload a cache archive with these crafted headers.

### Impact Explanation
An unprivileged job can craft a cache/artifact zip whose entry names traverse outside the extraction root, and via the `ZipUIDGidField` extra field, force the runner's cache/artifact-extraction helper process to `os.Lchown` and `lchmod` arbitrary paths reachable by traversal (e.g., files elsewhere on the same filesystem visible from the extraction process's working directory, such as another project's cache directory or shared helper binaries mounted into the same filesystem namespace). Combined with the file write from the first loop (classic zip-slip), this can plant a file at an attacker-chosen location with attacker-chosen ownership and permission bits, which is a concrete privilege-escalation/persistence primitive if that path is later read/executed by a process running as a different, more privileged UID (e.g., another job's helper process, or a mounted binary path).

### Likelihood Explanation
Feasibility is high and fully reproducible: crafting a zip with a traversal `Name` and a `ZipUIDGidField` extra field requires only standard tooling (writing raw zip headers, which is straightforward with Go's `archive/zip` or manual byte construction) and no special runner configuration. The main constraint is the effective privilege/reachability of the traversal target from the extraction process's working directory and filesystem namespace (shared runner filesystem, shared cache volumes, etc.) — this is an execution-environment-dependent but code-level unmitigated gap, not a documented/accepted risk.

### Recommendation
- In `extractZipFile` (and before performing any `os.Lchown`/`lchmod` in the second loop of `ExtractZipArchive`), resolve each `file.Name` against the extraction root using a safe join (e.g., verify the cleaned, joined path remains a descendant of the root via `filepath.Rel`/prefix check) and reject/skip entries that resolve outside it, mirroring protections typically applied to tar extraction (zip-slip guard).
- Apply the same containment check before calling `os.Lchown` in `processZipUIDGidField` and before `lchmod`, since these operate on the same untrusted `file.Name` independently of the write step.
- Add regression tests asserting that entries with `..`-containing or absolute `Name` values are rejected and that `lchown`/`lchmod` are never invoked on paths outside the extraction root.

### Proof of Concept
Go unit test sketch in `helpers/archives`:
```go
func TestExtractZipArchive_RejectsTraversalWithOwnershipChange(t *testing.T) {
    root := t.TempDir()
    outside := filepath.Join(filepath.Dir(root), "outside-target")
    defer os.Remove(outside)

    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)

    hdr := &zip.FileHeader{Name: "../outside-target"}
    hdr.SetMode(0o644)
    // Append a ZipUIDGidField extra field specifying an arbitrary UID/GID (e.g. 0/0)
    hdr.Extra = buildUIDGidExtra(0, 0)
    w, _ := zw.CreateHeader(hdr)
    _, _ = w.Write([]byte("payload"))
    _ = zw.Close()

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))

    originalWD, _ := os.Getwd()
    _ = os.Chdir(root)
    defer os.Chdir(originalWD)

    _ = ExtractZipArchive(zr)

    // Assert the file was NOT created/chowned/chmodded outside the root
    _, err := os.Stat(outside)
    assert.True(t, os.IsNotExist(err), "traversal entry must not be written outside extraction root")
}
```
Expected (current, vulnerable) result: the test fails because `outside-target` is created, and (on a privileged process) its ownership/mode are altered — demonstrating `lchmod`/`os.Lchown` execute on the traversal path. After the recommended fix, entries resolving outside the root must be rejected before any write, `lchmod`, or `Lchown` call.

### Citations

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

**File:** helpers/archives/zip_extract.go (L98-107)
```go
	for _, file := range archive.File {
		if err := lchmod(file.Name, file.Mode()); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}

		// Process zip metadata
		if err := processZipExtra(&file.FileHeader); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}
```

**File:** helpers/archives/zip_extra_unix.go (L37-48)
```go
func processZipUIDGidField(data []byte, file *zip.FileHeader) error {
	var ugField ZipUIDGidField
	err := binary.Read(bytes.NewReader(data), binary.LittleEndian, &ugField)
	if err != nil {
		return err
	}

	if !(ugField.Version == 1 && ugField.UIDSize == 4 && ugField.GIDSize == 4) {
		return errors.New("uid/gid data not supported")
	}

	return os.Lchown(file.Name, int(ugField.UID), int(ugField.Gid))
```

**File:** helpers/archives/os_unix.go (L12-28)
```go
func lchmod(name string, mode os.FileMode) error {
	var flags int

	if runtime.GOOS == "linux" {
		// Linux does not support changing modes on symlinks.
		if mode&os.ModeSymlink != 0 {
			return nil
		}
	} else {
		flags = unix.AT_SYMLINK_NOFOLLOW
	}

	err := unix.Fchmodat(unix.AT_FDCWD, name, uint32(mode.Perm()), flags)
	if err != nil {
		return &os.PathError{Op: "lchmod", Path: name, Err: err}
	}
	return nil
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

**File:** commands/helpers/cache_extractor.go (L646-663)
```go
	f, size, format, err := openArchive(c.File)
	if os.IsNotExist(err) {
		warningln("Cache file does not exist")
	}
	if err != nil {
		logrus.Fatalln(err)
	}
	defer f.Close()

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
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
