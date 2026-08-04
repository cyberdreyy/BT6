### Title
`Archive` preserves unrestricted symlink targets, enabling a poisoned artifact/cache archive that escapes the restore root - (File: commands/helpers/archive/ziplegacy/zip_legacy_archiver.go)

### Summary
`zip_legacy_archiver.go`'s `Archive` delegates entirely to `archives.CreateZipArchive`, which for symlink entries calls `os.Readlink` and writes the raw link target into the zip with no validation that the target stays inside the archived root. Because the corresponding extractor (`archives.ExtractZipArchive` / `extractZipSymlinkEntry`) recreates the symlink with that exact target and the zip extractor never even confines extraction to its assigned directory, an attacker-controlled symlink checked into or created in the job workspace becomes a persistent, root-escaping link once restored by a later cache/artifact download.

### Finding Description
`Archive` in `commands/helpers/archive/ziplegacy/zip_legacy_archiver.go` (lines 34-43) sorts the caller-supplied file map and calls `archives.CreateZipArchive(a.w, sorted)`. Inside `helpers/archives/zip_create.go`, `createZipEntry` dispatches symlink-mode files to `createZipSymlinkEntry` [1](#0-0)  which does:
```
link, err := os.Readlink(fh.Name)
...
_, err = io.WriteString(fw, link)
```
No check is made that `link` is relative, or that it resolves to a path inside the build/cache root. An unprivileged pipeline author fully controls this input: `before_script` can run `ln -s /etc/passwd leak` (or `ln -s ../../another-project/secret leak`) and then declare `artifacts: paths: [leak]` or a `cache: paths:` entry. `commands/helpers/file_archiver.go` (`process`/`findRelativePathInProject`) only validates that the symlink *file itself* lives inside the working directory [2](#0-1) ; it never inspects or restricts where the symlink *points*. The file gets included in `files map[string]os.FileInfo`, reaches `Archive`, and its raw, unrestricted `os.Readlink` target is embedded in the zip.

On the restore side, `helpers/archives/zip_extract.go`'s `extractZipSymlinkEntry` recreates the symlink verbatim:
```
err = os.Symlink(string(data), file.Name)
```
with no containment check [3](#0-2) . Worse, `zip_legacy_extractor.go`'s `Extract` calls `archives.ExtractZipArchive(zr)` and never passes or uses `e.dir` at all [4](#0-3) , unlike the tar/tarzstd extractor which enforces `!strings.HasPrefix(path, e.dir+separator)` [5](#0-4) . The zip legacy path only works today because callers `os.Chdir` into the target directory first (as documented in the test helper comment "hack: legacy archiver require being in the correct working dir" [6](#0-5) ), which confines the *entry name* but does nothing to confine the *symlink target*.

Net effect: the symlink node lands inside the restore root (satisfying entry-name containment), but it points arbitrarily outside that root. Any later step in the same job, or a downstream job/consumer, that reads/writes through that path (e.g. `cat leak`, or another archiving pass that follows the link) is redirected outside the intended build/cache/artifact root.

### Impact Explanation
A pipeline author can smuggle a dangling symlink into cache/artifact archives that, once restored in a later job (same or different pipeline, possibly running with different privileges or on a different runner instance sharing state), resolves to files outside the job's build directory. This enables cross-job/cross-boundary file read (e.g. leaking files elsewhere on the runner host readable by the job UID) or, if the same path is later written through, an out-of-root file write — a concrete restore-time path/containment escape as scoped by the question.

### Likelihood Explanation
Fully reachable by an unprivileged pipeline author: creating a symlink in `before_script`/`script` and listing it under `artifacts:paths` or `cache:paths` is standard `.gitlab-ci.yml` usage, requires no special runner configuration, and works with the default (legacy zip) archiver format. No existing check in `file_archiver.go`, `zip_create.go`, or `zip_extract.go` inspects or restricts symlink targets, so the attack is deterministic and repeatable.

### Recommendation
Reject or rewrite symlink targets at archive time in `createZipSymlinkEntry` (and equivalently in the tar/tarzstd archivers) unless `filepath.IsAbs(link)` is false and the resolved target stays within `a.dir` (e.g., verify `filepath.Rel(a.dir, filepath.Join(filepath.Dir(fh.Name), link))` does not start with `..`). Additionally, have `zip_legacy_extractor.go` pass and enforce `e.dir` containment identically to the tar/tarzstd extractors, and re-validate symlink targets again on extraction before calling `os.Symlink`.

### Proof of Concept
Go test in `helpers/archives`:
1. In a temp dir `srcDir`, create `srcDir/leak -> /etc/passwd` (absolute) via `os.Symlink("/etc/passwd", filepath.Join(srcDir, "leak"))`.
2. Call `CreateZipArchive` on `["leak"]` (cwd = `srcDir`), producing a zip.
3. Call `ExtractZipFile` into a fresh `dstDir` (cwd = `dstDir`).
4. Assert: `os.Readlink(filepath.Join(dstDir, "leak"))` returns `/etc/passwd`, and `os.Stat` of that path via the symlink resolves outside `dstDir` — proving the archived symlink target is preserved unrestricted and the restored link escapes the assigned root. Expected (fixed) behavior: `CreateZipArchive` should return an error or sanitize the target so the symlink cannot resolve outside `dstDir`.

### Citations

**File:** helpers/archives/zip_create.go (L17-30)
```go
func createZipSymlinkEntry(archive *zip.Writer, fh *zip.FileHeader) error {
	fw, err := archive.CreateHeader(fh)
	if err != nil {
		return err
	}

	link, err := os.Readlink(fh.Name)
	if err != nil {
		return err
	}

	_, err = io.WriteString(fw, link)
	return err
}
```

**File:** commands/helpers/file_archiver.go (L65-88)
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L24-33)
```go
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

**File:** commands/helpers/archiver_test.go (L66-70)
```go
		out := t.TempDir()

		// hack: legacy archiver require being in the correct working dir
		_ = os.Chdir(out)

```
