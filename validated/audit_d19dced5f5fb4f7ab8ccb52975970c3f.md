### Title
Legacy zip extractor performs chown/chtimes (and file writes) on unsanitized `file.Name` paths, enabling zip-slip outside the extraction root - ([File: helpers/archives/zip_extract.go, helpers/archives/zip_extra.go, helpers/archives/zip_extra_unix.go])

### Summary
`ExtractZipArchive` and its helpers (`extractZipFile`, `processZipExtra` → `processZipUIDGidField`/`processZipTimestampField`) use `zip.File.Name` directly in `os.Mkdir`, `os.OpenFile`, `os.Symlink`, `os.Lchown`, and `os.Chtimes` without any path-traversal sanitization (no `..`/absolute-path check, no confinement to a base directory). This is unlike the sibling `tarzstd` extractor in the same codebase, which explicitly validates that the resolved path stays inside the chroot before writing.

### Finding Description
`extractZipFile` in [1](#0-0)  calls `os.MkdirAll(filepath.Dir(file.Name), …)` and dispatches to `extractZipDirectoryEntry`/`extractZipSymlinkEntry`/`extractZipFileEntry`, all of which use `file.Name` verbatim (`os.Mkdir`, `os.Symlink`, `os.OpenFile`) — see [2](#0-1) . There is no check that `file.Name` is relative and does not contain `..` segments or an absolute path, so a crafted entry name (e.g. `../../etc/cron.d/x`) is honored as-is.

After the file-write pass, `ExtractZipArchive` calls `processZipExtra(&file.FileHeader)` for every entry — [3](#0-2) . `processZipExtra` parses the Info-ZIP UID/GID (`0x7875`) and Unix-timestamp (`0x5455`) extra fields and dispatches to `processZipUIDGidField`/`processZipTimestampField`, again passing the raw `file.Name` — [4](#0-3) . `processZipUIDGidField` calls `os.Lchown(file.Name, …)` [5](#0-4) , and `processZipTimestampField` calls `os.Chtimes(file.Name, …)` [6](#0-5) .

Since the UID/GID and timestamp values are attacker-authored fields embedded in the zip's own extra data (not derived from the extraction host), an attacker only needs to control the zip's central-directory `file.Name` (classic zip-slip) to make these metadata calls land on an arbitrary path relative to the process's CWD — completely independent of whether UID/GID spoofing itself is "useful"; the metadata operations simply follow wherever the unsanitized name points. `errorIfGitDirectory`/`isPathAGitDirectory` only special-case `.git` prefixes and provide no general traversal protection [7](#0-6) .

By contrast, the `tarzstd` extractor used for the same class of operation explicitly guards against this: it resolves `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects results that don't have `e.dir` as a prefix — [8](#0-7) . The legacy zip path (`helpers/archives/zip_extract.go`, wired up via `commands/helpers/archive/ziplegacy`) has no equivalent check, so it is inconsistent with the rest of the codebase's own established mitigation for exactly this class of bug.

This legacy zip extractor is reachable in production: it is always registered as the fallback decompressor for zstd-compressed zip caches/artifacts, and as the primary zip extractor whenever `FF_USE_FASTZIP` is disabled, per the registration comment in `commands/helpers/archiver.go` [9](#0-8) . Cache and artifact archives are attacker-influenced content: any job that can create/upload a cache or artifact zip (including one crafted with a `zip.Writer` that sets a traversal `Name` plus a `0x7875`/`0x5455` extra field, which the standard library and this custom writer freely allow) can produce a payload that is later extracted by a different job run (or the same job on a subsequent pipeline stage) using this extractor.

### Impact Explanation
When triggered, the extractor writes/creates a file/symlink at the attacker-chosen relative path (zip-slip) and then invokes `os.Chtimes`/`os.Lchown` against that same unsanitized path, letting the attacker set arbitrary ownership (via UID/GID) and modification/access timestamps on files outside the intended cache/artifact extraction root — a direct violation of the "file operations must stay within intended build/cache/artifact roots" invariant. Depending on the executor and where extraction runs (host-side vs. inside the job's own container/helper), this can extend to overwriting or re-owning files outside the job's build directory.

### Likelihood Explanation
This requires only the ability to produce a zip archive that is later fed to `ExtractZipArchive`/`ExtractZipFile` (cache push then cache pull, or artifact upload then artifact download in a downstream job) — actions an ordinary pipeline author can perform. No admin privilege or special runner configuration is needed beyond the legacy zip extractor being in use (default when `FF_USE_FASTZIP` is off, or always for zstd-compressed zip decompression fallback). The bug is fully deterministic and repeatable.

### Recommendation
Add path-confinement validation in `extractZipFile` (and reuse it before calling `processZipExtra`) analogous to the check already used in `tarzstd_extractor.go`: resolve each `file.Name` against the intended extraction root with `filepath.Join`/`filepath.Abs`, reject/skip entries whose resolved path is not a descendant of that root (and reject absolute names and any path containing `..` that escapes it) before performing `Mkdir`, `OpenFile`, `Symlink`, `Chtimes`, or `Lchown`.

### Proof of Concept
Go unit test in `helpers/archives`:
```go
func TestExtractZipArchive_PathTraversalUIDGid(t *testing.T) {
    tmpDir := t.TempDir()
    outsideFile := filepath.Join(filepath.Dir(tmpDir), "zip-slip-outside-file")
    defer os.Remove(outsideFile)

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    fh := &zip.FileHeader{Name: "../zip-slip-outside-file"}
    fh.SetMode(0o644)
    // attach UID/GID + timestamp extra fields
    fh.Extra = createZipExtra(fakeFileInfo{}) // or hand-crafted 0x7875/0x5455 bytes
    w, _ := zw.CreateHeader(fh)
    w.Write([]byte("pwn"))
    zw.Close()

    r, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    chdirTo(tmpDir) // simulate extraction root
    err := ExtractZipArchive(r)
    require.NoError(t, err)

    _, statErr := os.Stat(outsideFile)
    assert.True(t, os.IsNotExist(statErr), "file must not be created/chowned/chtimed outside the extraction root")
}
```
Expected (current, buggy) result: `outsideFile` is created and `os.Lchown`/`os.Chtimes` succeed on it, proving escape. After the fix, the entry should be rejected/skipped and the assertion should pass.

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

**File:** helpers/archives/zip_extra.go (L50-68)
```go
func processZipTimestampField(data []byte, file *zip.FileHeader) error {
	if !file.Mode().IsDir() && !file.Mode().IsRegular() {
		return nil
	}

	var tsField ZipTimestampField
	err := binary.Read(bytes.NewReader(data), binary.LittleEndian, &tsField)
	if err != nil {
		return err
	}

	if (tsField.Flags & 1) == 1 {
		modTime := time.Unix(int64(tsField.ModTime), 0)
		acTime := time.Now()
		return os.Chtimes(file.Name, acTime, modTime)
	}

	return nil
}
```

**File:** helpers/archives/zip_extra.go (L96-121)
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

**File:** commands/helpers/archiver.go (L19-37)
```go
func init() {
	// enable fastzip archiver/extractor
	logger := logrus.WithField("name", featureflags.UseFastzip)
	if on := featureflags.IsOn(logger, os.Getenv(featureflags.UseFastzip)); on {
		archive.Register(archive.Zip, fastzip.NewArchiver, fastzip.NewExtractor)

		// The default zstd compressor is fastzip, this is registered via the
		// fastzip implementation (helpers/archive/fastzip).
		//
		// The default zstd decompressor is the legacy zip implementation (helpers/archive/ziplegacy).
		// This intended to allow the default zip implementation to still be able to decompress zstd,
		// even if it is unable to compress it (only fastzip can compress). This also allows the older
		// extraction behaviour to be enabled.
		//
		// Here we're registering the decompress only if FF_USE_FASTZIP is enabled. This overrides
		// the ziplegacy zstd support.
		archive.Register(archive.ZipZstd, nil, fastzip.NewExtractor)
	}
}
```
