### Title
Zip-slip path traversal in `ziplegacy` zip extractor - unsanitized `file.Name` used for write/chown outside `dir` - ([File: helpers/archives/zip_extract.go], [File: commands/helpers/archive/ziplegacy/zip_legacy_extractor.go])

### Summary
The `ziplegacy` extractor's `Extract` method discards its own `dir` field and calls `archives.ExtractZipArchive(zr)` directly, and the underlying `extractZipFile`/`processZipUIDGidField` functions use `file.Name` verbatim (never joined with, or validated against, `dir`). A job that controls the cache/artifact zip contents (including cache archives it uploads to the shared cache) can supply an absolute path or a `../`-containing entry name and cause writes and ownership changes on the host filesystem outside the intended extraction directory.

### Finding Description
`ziplegacy.extractor.Extract` is defined as: [1](#0-0) 
`e.dir` (set from `NewExtractor`'s `dir` parameter) is stored but never referenced inside `Extract`; it calls `archives.ExtractZipArchive(zr)` with no directory argument at all.

`ExtractZipArchive` iterates zip entries and calls `extractZipFile(file)` and `processZipExtra(&file.FileHeader)` using `file.Name` directly: [2](#0-1) [3](#0-2) 
`extractZipFile` performs `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then, depending on entry type, `os.Mkdir`, `os.Symlink`, or `os.OpenFile`/`io.Copy` — all using `file.Name` unmodified (`extractZipDirectoryEntry`, `extractZipSymlinkEntry`, `extractZipFileEntry`, lines 12-59 of the same file). The only validation performed anywhere in the loop is `errorIfGitDirectory`, which only warns about a `.git` directory being present — it performs no path-traversal or absolute-path check.

After file creation, the second loop calls `processZipExtra`, which for the UID/GID extra field ultimately calls `os.Lchown(file.Name, uid, gid)`: [4](#0-3) 
Again `file.Name` is used verbatim with no path confinement to `dir`.

The caller that exercises this path with attacker-controlled zip content is `CacheExtractorCommand.Execute`, which builds the extractor with `wd` (the job's working directory) as `dir` and then calls `Extract`: [5](#0-4) 
Since a job (or a compromised job/whoever can push to the shared cache path used by that job) fully controls the contents of the cache zip file that gets downloaded/opened here, and since the `ziplegacy` extractor is registered for the `zip` format via `archive.Register`/`archive.NewExtractor` (contrasted with `fastzip`, which passes `e.dir` to `fastzip.NewExtractorFromReader` and is presumably safe), a zip entry named e.g. `../../../tmp/evil` or `/etc/cron.d/evil` bypasses the intended confinement to `dir` entirely because `dir` is simply unused.

Existing protections reviewed and found insufficient:
- `errorIfGitDirectory` — only checks for `.git`, not path traversal/absolute paths.
- No `filepath.Clean`, `filepath.IsAbs`, or "is under dir" check exists anywhere in `helpers/archives/zip_extract.go` or `ziplegacy/zip_legacy_extractor.go`.
- `pathErrorTracker` (`tracker.actionable`) only suppresses repeated warning logs; it does not block or validate paths.

### Impact Explanation
An unprivileged pipeline author who controls cache (or any zip archive processed by this code path) contents can cause the Runner host process to create arbitrary directories, write arbitrary file contents, and change ownership (`os.Lchown`) at arbitrary filesystem locations reachable by the Runner process's privileges — outside the job's workspace/cache directory. This is a host filesystem write + ownafter impact scoped exactly to "File operations must stay within intended build/cache/artifact roots," which is violated.

### Likelihood Explanation
- Precondition: the `zip` format is routed through the `ziplegacy` extractor (registered via `archive.Register`) rather than `fastzip`. This is reachable whenever `ziplegacy` is the active handler for `Zip` format zip archives handled by this binary (e.g. environments/builds where `ziplegacy` is registered instead of/in addition to `fastzip`).
- The attacker fully controls the cache archive's zip entry names (cache upload is driven by the job itself uploading its own cache archive, and `cache_extractor.go` simply downloads and extracts whatever zip content is present at the cache URL for that job).
- No sanitization exists in the extraction path, so the exploit is deterministic and repeatable — every run of `Extract()` against a malicious archive with a traversal/absolute-path entry will attempt the malicious file operation.
- Runner process privileges (e.g., running as a privileged user on the Runner host, or in shell/docker executors where the Runner agent itself has broader host access) determine how damaging the resulting write/chown is, but the write-outside-dir behavior itself is unconditional given this code path is used.

### Recommendation
In `ExtractZipArchive` (or, more architecturally, in `ziplegacy.extractor.Extract`), sanitize and confine every `file.Name` to the target `dir`:
- Reject entries with absolute paths or that `filepath.Clean` resolves outside `dir` (classic zip-slip guard: `target := filepath.Join(dir, file.Name); if !strings.HasPrefix(target, filepath.Clean(dir)+string(os.PathSeparator)) { return error }`).
- Thread `dir` through `ExtractZipArchive`/`extractZipFile`/`processZipExtra` (and equivalents in `zip_extra_windows.go`) so all `os.Mkdir*`, `os.Symlink`, `os.OpenFile`, and `os.Lchown` calls operate on the joined, validated path rather than the raw `file.Name`.
- Add this same validation for symlink targets (`extractZipSymlinkEntry`) since a symlink pointing outside `dir` combined with a later legitimate write could also escape confinement.

### Proof of Concept
```go
package ziplegacy

import (
    "archive/zip"
    "bytes"
    "os"
    "path/filepath"
    "testing"

    "github.com/stretchr/testify/require"
)

func TestExtract_PathTraversal_Blocked(t *testing.T) {
    dir := t.TempDir()
    workDir := filepath.Join(dir, "workspace", "cache")
    require.NoError(t, os.MkdirAll(workDir, 0o755))

    // Build a malicious zip with a traversal entry.
    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    w, err := zw.Create("../../../tmp/evil")
    require.NoError(t, err)
    _, err = w.Write([]byte("pwned"))
    require.NoError(t, err)
    require.NoError(t, zw.Close())

    r := bytes.NewReader(buf.Bytes())
    ext, err := NewExtractor(r, int64(buf.Len()), workDir)
    require.NoError(t, err)

    err = ext.Extract(context.Background())
    // Expected (after fix): Extract returns an error, and no file exists outside workDir.
    require.Error(t, err)
    _, statErr := os.Stat(filepath.Join(dir, "tmp", "evil"))
    require.True(t, os.IsNotExist(statErr), "malicious file must not be created outside dir")
}
```
Currently, with the unfixed code, `ext.Extract(...)` returns `nil` and `filepath.Join(dir, "tmp", "evil")` (i.e., a path outside `workDir`) is created, demonstrating the escape.

### Citations

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L26-32)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
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

**File:** helpers/archives/zip_extra_unix.go (L37-49)
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
}
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
