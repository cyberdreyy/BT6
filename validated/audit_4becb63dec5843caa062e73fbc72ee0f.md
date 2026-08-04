Confirmed: no path-traversal sanitization exists anywhere in the zip extraction path. `file.Name` from the zip entry is used verbatim in `extractZipFile` for `os.MkdirAll(filepath.Dir(file.Name), ...)` and `os.OpenFile(file.Name, ...)`, and the only path check present, `errorIfGitDirectory`, only rejects `.git` prefixes, not `../` traversal.

### Title
Zip extraction path traversal allows `os.Lchown` (and file write) outside the job workspace via crafted `file.Name` - ([File: helpers/archives/zip_extract.go, helpers/archives/zip_extra_unix.go])

### Summary
`ExtractZipArchive` and `extractZipFile` use the zip entry's `file.Name` unsanitized to create directories/files, and `processZipUIDGidField` subsequently calls `os.Lchown(file.Name, UID, Gid)` on that same unsanitized name. Since no code path checks for `../` segments or resolves/pins the target path within the extraction root, a crafted zip with a traversal `file.Name` combined with a `0x7875` extra field lets an attacker's zip both write a file and `chown` it outside the intended extraction directory.

### Finding Description
`extractZipFile` (helpers/archives/zip_extract.go:61-83) does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then, depending on file type, `os.OpenFile(file.Name, ...)` (extractZipFileEntry) or `os.Symlink(...)`/`os.Remove(file.Name)` — all using the raw, attacker-controlled `zip.File.Name` string. The only sanity check performed before extraction is `errorIfGitDirectory` (helpers/archives/path_check_helper.go:13-19), which only rejects paths whose first cleaned segment is `.git`; it does nothing to reject `..` traversal segments or absolute paths. `ExtractZipArchive` then iterates the same files a second time and calls `processZipExtra(&file.FileHeader)` → `processZipUIDGidField(data, file)` (helpers/archives/zip_extra_unix.go:37-48), which calls `os.Lchown(file.Name, int(ugField.UID), int(ugField.Gid))` again with the same raw name. Since Go's `archive/zip` package does not itself prevent `../`-containing names, and this codebase never calls `filepath.Clean`/validates that the resolved path stays under the extraction root, a crafted entry named e.g. `../../etc/passwd_shadowcopy` with a `0x7875` extra field will be written outside the workspace and then have its ownership changed by `os.Lchown` to attacker-chosen UID/GID.

### Impact Explanation
If the job/helper process has the required privileges (matching ownership or `CAP_CHOWN`), a malicious cache or artifact zip can write/overwrite a file outside the job's working directory and change its ownership, corrupting host or shared-runner state outside the sandboxed job root — consistent with the scoped impact of unauthorized file/ownership modification and cross-tenant workspace corruption on shared runners.

### Likelihood Explanation
This requires only that an unprivileged pipeline author can supply a cache/artifact zip that the runner will extract via `ExtractZipFile`/`ExtractZipArchive` (this is a normal, common runner operation for job caches/artifacts) and that the process extracting it has ownership/CAP_CHOWN over the traversal target — a condition that is plausible for shared runner hosts with predictable paths, or where the traversal targets files already owned by the job/helper user. The exploit is fully deterministic and repeatable: it only requires crafting a zip byte-for-byte (standard zip tooling supports embedding non-normalized `../` names and custom extra fields).

### Recommendation
Before any file operation in `extractZipFile`, canonicalize the destination (e.g. `filepath.Join(extractionRoot, file.Name)` then verify via `filepath.Rel`/prefix check that the resulting path stays within `extractionRoot`), rejecting or skipping entries that escape it — the same validation should gate `processZipUIDGidField`/`processZipTimestampField` (and the tar extractor if it has the analogous gap), not just the `.git`-specific check.

### Proof of Concept
Go unit test in `helpers/archives`:
1. Build an in-memory `zip.Writer`, add one entry named `../evil_target` (regular file) containing arbitrary bytes, with a raw extra field of type `0x7875` (`ZipUIDGidFieldType`) encoding `Version=1, UIDSize=4, UID=<test-uid>, GIDSize=4, GID=<test-gid>`.
2. Extract the zip inside a temp "workspace" subdirectory using `ExtractZipArchive`.
3. Assert: (a) `../evil_target` (i.e., a file adjacent to/outside the workspace dir) was created — demonstrating the path escape via `extractZipFileEntry`; (b) call `os.Lstat` on that escaped file and check its `Uid/Gid` (via `syscall.Stat_t`) equal the attacker-supplied values — demonstrating `os.Lchown` executed on the out-of-root path. A correct fix should cause `ExtractZipArchive` to reject the entry (no file created outside workspace) and the test should then assert absence of the escaped file/no chown occurred. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
