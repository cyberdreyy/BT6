### Title
`extractZipFile` allows symlink-based path escape via in-archive symlink pivots - (File: `helpers/archives/zip_extract.go`)

### Summary
`extractZipFile` and its helper `extractZipSymlinkEntry` create symlinks from zip entries with no validation of the link target or of whether later entries use that link as a path prefix to write outside the extraction root. An attacker who controls the archive content (their own job's cache/artifact) can plant a symlink entry pointing to an absolute or `../`-escaping path, then a subsequent file entry whose name uses the symlink as a directory component, causing the write to land outside the intended restore root.

### Finding Description
`extractZipFile` [1](#0-0)  dispatches based on `file.Mode()`. For `os.ModeSymlink` entries it calls `extractZipSymlinkEntry`, which reads the link target directly from the zip entry's file content and calls `os.Symlink(string(data), file.Name)` with no validation that the target stays within the restore root [2](#0-1) . For regular files, `extractZipFileEntry` removes any existing entry at `file.Name` and then opens/writes it [3](#0-2) .

The `os.Remove(file.Name)` call only removes the leaf component if `file.Name` matches exactly; it does not protect intermediate path components. If entry #1 in the archive is a symlink named e.g. `linkdir` pointing to an absolute trusted path (e.g. `/home/gitlab-runner/builds/other-project`), and entry #2 is a regular file named `linkdir/payload`, then in `extractZipFile` the call `os.MkdirAll(filepath.Dir(file.Name), 0o777)` [4](#0-3)  resolves `linkdir` as an existing directory (by following the symlink), succeeds without error, and then `extractZipFileEntry` opens `linkdir/payload` for writing, which the OS resolves through the symlink to the trusted external path.

The only validation performed anywhere in `ExtractZipArchive` is `errorIfGitDirectory`, which only blocks `.git` as a path prefix [5](#0-4)  and is unrelated to symlink or path-traversal containment. There is no check that a symlink's target is within the extraction root, no check that intermediate path components are not symlinks, and no absolute-path/`..` rejection for either symlink targets or entry names in `ExtractZipArchive` [6](#0-5) .

Both artifact/cache extraction paths route through this code: the legacy zip extractor calls `archives.ExtractZipArchive` directly [7](#0-6) .

### Impact Explanation
A job author who fully controls their own cache/artifact zip content can, upon extraction (by their own job, a later job in the same pipeline/project sharing cache, or a maintainer's job pulling the artifact), write attacker-controlled file content to a location outside the designated cache/artifact/build root — e.g. into another project's build directory, a shared cache directory, or any world/group-writable path reachable via symlink target on the runner host filesystem. This matches the scoped impact of "cross-job tampering or secret exposure through symlink escape," since a write landing in another job's workspace could tamper with that job's data, and if runner file permissions allow, could overwrite files later read by a different job (secret exposure/tampering).

### Likelihood Explanation
The precondition is only that the attacker can supply an artifact/cache zip archive that Runner will extract — a capability every ordinary pipeline author has (their own `.gitlab-ci.yml` job produces the cache/artifact zip, or dependent-job downloads consume an artifact built by an earlier stage they control). No special privileges, admin action, or externally compromised system is required. The exploit is purely a crafted-archive content issue: two entries (symlink + file-through-symlink) — highly repeatable and does not depend on race conditions or timing.

### Recommendation
- In `extractZipSymlinkEntry`, resolve the symlink target against the extraction root and reject symlink entries whose resolved target escapes the root (absolute paths and `..` sequences that leave the root).
- In `extractZipFile`/`ExtractZipArchive`, before creating parent directories or writing any entry, verify that no path component of `file.Name` (after `filepath.Clean`) resolves through an existing symlink to a location outside the extraction root — e.g. walk the path components and use `os.Lstat` to detect and reject symlinks at any intermediate segment, or extract into a fresh subdirectory and use `filepath.EvalSymlinks`-based containment checks similar to standard zip-slip mitigations.
- Reject entries with absolute paths or entries whose cleaned path is not lexically inside the extraction root, consistent with Go's `archive/tar`/`zip` slip-prevention guidance.

### Proof of Concept
Go unit test in `helpers/archives/zip_extract_test.go` style:
1. Create a temp extraction root `root`.
2. Build a zip in memory with two entries:
   - `evil_link` — mode `os.ModeSymlink`, content = absolute path to a temp "trusted" directory created outside `root` (e.g. `/tmp/trusted`), containing a canary file `secret.txt` with known content.
   - `evil_link/pwn.txt` — regular file with attacker payload content.
3. `cd` into `root` (or set extraction working directory to `root`) and call `archives.ExtractZipArchive` on the built reader.
4. Assert: `/tmp/trusted/pwn.txt` does NOT exist (extraction must not have followed the symlink outside `root`); if it does exist with attacker content, the vulnerability is confirmed.
5. Additionally assert `/tmp/trusted/secret.txt` content is unmodified, demonstrating no tampering of files outside the restore root.

### Citations

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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L26-32)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
```
