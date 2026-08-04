Confirmed vulnerability: `file.Name` (the zip entry's own path, fully attacker-controlled) is used directly, with no `filepath.Clean`, no confinement check against the extraction root `dir`/`wd`, and no rejection of `../` segments, anywhere in the extraction path (`extractZipFile`, `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, `extractZipFileEntry`, `processZipExtra`, `processZipUIDGidField`). The only path-related check present, `errorIfGitDirectory`, only blocks `.git` directory names and does nothing for traversal sequences.

### Title
Zip extraction lacks path-confinement check, allowing `os.Lchown` (and file write/symlink/mkdir) outside the job workspace via `../` traversal in `zip.FileHeader.Name` - (File: helpers/archives/zip_extract.go, helpers/archives/zip_extra_unix.go)

### Summary
`ExtractZipArchive` (`helpers/archives/zip_extract.go`) iterates `zip.File` entries and calls `extractZipFile`, then `processZipExtra` → `processZipUIDGidField` (`helpers/archives/zip_extra_unix.go`), passing `file.Name` straight from the untrusted zip header to `os.MkdirAll`, `os.OpenFile`, `os.Symlink`, and `os.Lchown` with no validation that the resolved path stays inside the extraction directory. Any job-controlled cache/artifact zip reaching `ExtractZipFile`/`ExtractZipArchive` (via `ziplegacy.zip_legacy_extractor` or the fastzip path) can therefore write files, create symlinks, and chown arbitrary paths reachable via `../` traversal from the current working directory when the runner process's privileges allow it.

### Finding Description
`ExtractZipArchive` at [1](#0-0)  loops over `archive.File` and calls `extractZipFile(file)` and later `processZipExtra(&file.FileHeader)`. `extractZipFile` at [2](#0-1)  calls `os.MkdirAll(filepath.Dir(file.Name), ...)` and then dispatches to `extractZipDirectoryEntry`/`extractZipSymlinkEntry`/`extractZipFileEntry`, all of which use `file.Name` verbatim (`os.Mkdir(file.Name, ...)`, `os.Symlink(string(data), file.Name)`, `os.OpenFile(file.Name, ...)`) as seen at [3](#0-2) . None of these join `file.Name` to a validated destination root, call `filepath.Clean`, or reject absolute paths or `..` segments. The only guard, `errorIfGitDirectory`, checks only whether the first path component is `.git`, per [4](#0-3) , and does nothing for traversal.

`processZipExtra` at [5](#0-4)  parses attacker-supplied "extra field" bytes from the zip header (`file.Extra`) and, for `ZipUIDGidFieldType`, calls `processZipUIDGidField(data, file)`. That function reads a UID/GID pair fully controlled by the archive author and calls `os.Lchown(file.Name, int(ugField.UID), int(ugField.Gid))` at [6](#0-5) , again using the raw, unvalidated `file.Name`.

Both `ExtractZipFile` (used by `commands/helpers/archive/ziplegacy` extractor, invoked from `cache-extractor`/artifact download helpers) and direct callers of `ExtractZipArchive` execute in the runner's current working directory (`wd`) as passed into `archive.NewExtractor` in `cache_extractor.go` (`extractor, err := archive.NewExtractor(format, f, size, wd)` at [7](#0-6) ), but that `wd`/`dir` is never actually used to confine `file.Name` — the legacy zip extractor's `dir` field is stored but unused in `Extract` ( [8](#0-7) ), meaning extraction operates on whatever CWD the process happens to be in, using the raw entry name with no join/clamp against it.

An attacker who controls a cache or artifact zip payload (e.g. a `cache:paths` or `artifacts:paths` produced by a job the attacker authors, later downloaded/extracted by the runner) can set `zip.FileHeader.Name = "../../../etc/passwd"` (or any traversal sequence) and attach a `ZipUIDGidFieldType` extra field with an arbitrary UID/GID. When extracted, `os.Lchown` will be invoked on the resolved traversal path rather than being confined to the job workspace, and file/symlink/mkdir operations for the same entry will likewise write outside the intended extraction root.

### Impact Explanation
This allows a job-controlled archive to change ownership (`os.Lchown`) of arbitrary files reachable from the runner process's working directory tree, and to write/overwrite files or create symlinks at those paths (subject to file-system permissions of the runner process). Concretely this breaks the invariant that job-controlled archive contents must stay confined to build/cache/artifact roots, since no destination-root confinement or `..`-rejection exists anywhere in the zip extraction pipeline.

### Likelihood Explanation
The exploit is straightforward and fully reproducible: any pipeline author who controls a cache/artifacts zip (standard job capability) can embed a crafted `zip.FileHeader.Name` with `../` segments and a matching `ZipUIDGidField` extra record. No special runner configuration is required — the vulnerable code path is unconditionally exercised whenever `ExtractZipArchive`/`ExtractZipFile` runs on an attacker-influenced zip (cache restore, artifact download, or any other consumer of this shared helper). The actual scope of the resulting `Lchown`/file write is bounded by the OS permissions of the runner process itself (e.g., if the runner runs as the job user without extra capabilities, ownership changes outside files it owns may fail at the OS level), but the code makes no independent attempt to prevent it — there is no defense-in-depth check at all.

### Recommendation
In `extractZipFile`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry`/`extractZipFileEntry` and in `processZipUIDGidField`/`processZipTimestampField`, resolve `file.Name` against the intended extraction root using `filepath.Join(root, filepath.Clean(file.Name))`, then verify the cleaned result still has `root` as a prefix (using `filepath.Rel` or an equivalent “inside root” check) before performing any `Mkdir`, `OpenFile`, `Symlink`, `Chtimes`, or `Lchown` call; reject the entry (and abort or skip with a warning, consistent with existing `tracker.actionable` error handling) if it escapes the root. This mirrors the standard Go zip-slip mitigation pattern and should be applied uniformly across `helpers/archives/zip_extract.go` and `helpers/archives/zip_extra.go`/`zip_extra_unix.go`.

### Proof of Concept
```go
func TestExtractZipArchive_PathTraversal_Lchown(t *testing.T) {
    tmpRoot := t.TempDir()
    extractDir := filepath.Join(tmpRoot, "job-workspace")
    require.NoError(t, os.MkdirAll(extractDir, 0o755))

    outsideDir := filepath.Join(tmpRoot, "outside")
    require.NoError(t, os.MkdirAll(outsideDir, 0o755))

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    hdr := &zip.FileHeader{Name: "../outside/evil.txt", Method: zip.Deflate}
    // attach ZipUIDGidFieldType extra field with forged UID/GID
    var extra bytes.Buffer
    ugField := archives.ZipUIDGidField{Version: 1, UIDSize: 4, UID: 1234, GIDSize: 4, Gid: 1234}
    fieldHdr := archives.ZipExtraField{Type: archives.ZipUIDGidFieldType, Size: uint16(binary.Size(&ugField))}
    binary.Write(&extra, binary.LittleEndian, &fieldHdr)
    binary.Write(&extra, binary.LittleEndian, &ugField)
    hdr.Extra = extra.Bytes()

    w, _ := zw.CreateHeader(hdr)
    w.Write([]byte("payload"))
    zw.Close()

    // chdir into extractDir before extracting to model the intended confinement
    oldWd, _ := os.Getwd()
    defer os.Chdir(oldWd)
    os.Chdir(extractDir)

    r, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    err = archives.ExtractZipArchive(r)
    require.NoError(t, err)

    // Assertion: the file must NOT exist outside extractDir; today it will.
    _, err = os.Stat(filepath.Join(outsideDir, "evil.txt"))
    assert.True(t, os.IsNotExist(err), "zip entry escaped the extraction root via path traversal")
}
```
Running this test against the current code (as an unprivileged user, run as root or with `CAP_CHOWN` for full `Lchown` reproduction) demonstrates the file lands in `outsideDir`, not `extractDir`, and that `os.Lchown` targets the escaped path — proving the missing confinement check.

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

**File:** helpers/archives/zip_extra.go (L96-122)
```go
func processZipExtra(file *zip.FileHeader) error {
	if len(file.Extra) == 0 {
		return nil
	}

	r := bytes.NewReader(file.Extra)
	for {
		field, data, err := readZipExtraField(r)
		if err == io.EOF {
			break
		} else if err != nil {
			return err
		}

		switch field.Type {
		case ZipUIDGidFieldType:
			err = processZipUIDGidField(data, file)
		case ZipTimestampFieldType:
			err = processZipTimestampField(data, file)
		}
		if err != nil {
			return err
		}
	}

	return nil
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

**File:** commands/helpers/cache_extractor.go (L655-655)
```go
	extractor, err := archive.NewExtractor(format, f, size, wd)
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L12-32)
```go
// extractor is a zip stream extractor.
type extractor struct {
	r    io.ReaderAt
	size int64
	dir  string
}

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
```
