### Title
TOCTOU between path-containment validation in `fileArchiver.process`/`enumerate` and actual file read in archiver (`Archive`) allows symlink-swap to read files outside `c.wd` - ([File: commands/helpers/file_archiver.go], [File: commands/helpers/archive/raw/raw_archiver.go])

### Summary
`fileArchiver.process` and `findRelativePathInProject` validate that a *path* resolves inside `c.wd` and record a stale `os.FileInfo` (via `os.Lstat`) in `c.files` at enumeration time [1](#0-0) . The actual file content is read later, by path (not by fd or handle), inside a goroutine spawned from `createBodyProvider` that calls `archiver.Archive(ctx, c.files)` [2](#0-1) . Concrete archiver implementations such as the raw archiver call `os.Open(pathname)` directly using only the map key (path string), with no re-validation of containment or symlink status [3](#0-2) , meaning an attacker who swaps a regular file for a symlink between enumeration and archiving can have Runner transparently follow the symlink and read/upload content from outside `c.wd`.

### Finding Description
The validation path is: `enumerate()` → `processPaths()`/`processUntracked()` → `process(match)`, which computes `filepath.Rel(c.wd, absolute)` and rejects paths starting with `..`, then calls `add(relative)`, which does `os.Lstat(path)` and stores `c.files[path] = info` [4](#0-3) . This check only validates the *path string* is lexically within `c.wd` at that moment — it does not open, pin, or hold any file descriptor, and `os.Lstat` deliberately does not follow the final symlink component so a symlink is recorded correctly as a symlink at that point.

Later, in a separate goroutine, `ArtifactsUploaderCommand.createBodyProvider` invokes `archiver.Archive(context.Background(), c.files)` [2](#0-1) . For the `raw` format, `Archive` re-reads by opening the path with `os.Open(pathname)`, which *does* follow symlinks transparently, using only the string key from the map — the stale `os.FileInfo` captured earlier is not used to gate this open at all [5](#0-4) . So if, between `add()`'s `Lstat` and `Archive()`'s `Open`, an attacker-controlled concurrent process replaces the regular file at that path with a symlink pointing outside `c.wd` (e.g., `/etc/passwd`), `os.Open` will happily open and read the symlink target, and its content ends up in the artifact body stream that gets uploaded.

For the `zip`/`zipzstd` format (via the vendored `saracen/fastzip` library), the archiver is handed the stale `os.FileInfo` map directly and the library's own internal logic decides, per entry, whether to treat the entry as a symlink (using `Readlink`) or a regular file (using `Open`) based on that FileInfo's mode bits *captured at enumeration time*. The vendored library source is not present in this index, so the exact per-format behavior for fastzip could not be directly confirmed here, but the `raw` archiver's source, which is fully visible, unambiguously reproduces the flaw: containment/type is checked once at enumeration, and content is read later purely by path with no re-check.

No re-validation step (no repeat of `filepath.Rel`/`findRelativePathInProject`, no `O_NOFOLLOW`, no comparing `Lstat` result at read-time against the value captured at enumeration time) exists between enumeration and archiving. This is a genuine, currently-unmitigated time-of-check/time-of-use gap.

### Impact Explanation
A pipeline job that can run a background process (or a second job step) racing the artifact upload can cause the runner process (running with the job's own credentials/privileges within the executor) to read and upload the contents of a file outside the intended build root as part of the job's artifact archive. Since artifacts are downloadable by other pipeline participants with appropriate permissions, this can result in disclosure of host or executor filesystem content that the job should not have access to, packaged as an artifact under the job's own identity. The severity is bounded by what other files the job's OS user/container already has read permission to access via `os.Open` — this is not a sandbox-escape of file permissions, but a bypass of Runner's own workspace-containment intent for artifact uploads.

### Likelihood Explanation
Exploitation requires the attacker to control job execution (any pipeline author already does) and to win a narrow race: swap a file for a symlink in the small window between the `os.Lstat` call in `add()` during enumeration and the later `os.Open`/archive read in the upload goroutine. This window is real but typically small (bounded by enumeration time for remaining files plus artifact-archiver startup), and enumeration of large file sets or many artifact paths increases the window and repeatability. It is a genuine race, not a deterministic single-shot bug, but is reproducible with a tight polling loop replacing the file in a loop during the window, consistent with classic TOCTOU symlink attacks.

### Recommendation
Eliminate the path-based re-read: after `add()` captures `os.Lstat` info, open the file immediately (e.g., with `os.OpenFile(path, os.O_RDONLY, 0)` and `O_NOFOLLOW` where supported, or `os.Lstat` + explicit symlink rejection) and either (a) keep the file descriptor open and pass `*os.File` handles through to the archiver instead of paths/`os.FileInfo`, or (b) at minimum, immediately before opening for read in `Archive`, re-`Lstat` the path and compare device/inode and mode against the value recorded at enumeration time, rejecting the entry if it changed or is now a symlink pointing outside `dir`. Apply this consistently across all archiver implementations (`raw`, `fastzip` zip/zipzstd, tarzstd, gziplegacy, ziplegacy).

### Proof of Concept
Go integration test in `commands/helpers`:
1. Create `wd/secret_placeholder.txt` with benign content; enumerate it via `fileArchiver.enumerate()` so `c.files["secret_placeholder.txt"]` is populated with an `Lstat`-derived `os.FileInfo` for a regular file.
2. Immediately after `enumerate()` returns (simulating the race window), in a separate goroutine/test step, `os.Remove("wd/secret_placeholder.txt")` and `os.Symlink("/etc/passwd", "wd/secret_placeholder.txt")`.
3. Call `raw.NewArchiver(...).Archive(ctx, c.files)` (or drive it through `ArtifactsUploaderCommand.createBodyProvider`'s `ReaderFactory`) and capture the resulting stream.
4. Assert the resulting artifact content equals `/etc/passwd`'s content rather than the original placeholder content — proving the containment check performed at enumeration time was bypassed by the later, unchecked, path-based read.

### Citations

**File:** commands/helpers/file_archiver.go (L65-101)
```go
func (c *fileArchiver) process(match string) bool {
	var absolute, relative string
	var err error

	absolute, err = filepath.Abs(match)
	if err == nil {
		// Let's try to find a real relative path to an absolute from working directory
		relative, err = filepath.Rel(c.wd, absolute)
	}

	if err == nil {
		// Process path only if it lives in our build directory
		if !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			excluded, rule := c.isExcluded(relative)
			if excluded {
				c.exclude(rule)
				return false
			}

			err = c.add(relative)
		} else {
			err = errors.New("not supported: outside build directory")
		}
	}

	if err == nil {
		return true
	}

	if os.IsNotExist(err) {
		// We hide the error that file doesn't exist
		return false
	}

	logrus.Warningf("%s: %v", match, err)
	return false
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

**File:** commands/helpers/artifacts_uploader.go (L122-126)
```go
			// Start a new Goroutine to create the archive for this attempt
			go func() {
				archiveErr := archiver.Archive(context.Background(), c.files)
				pw.CloseWithError(archiveErr)
			}()
```

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
