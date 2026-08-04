### Title
`extractZipFile` calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` with an unrestricted, umask-dependent mode - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile` creates every parent directory for a zip entry with a hardcoded mode of `0o777`, and the resulting on-disk permissions depend entirely on the process umask rather than any Runner-enforced restriction. There is no path validation (no `..`/absolute-path rejection, only a `.git` check) in `ExtractZipArchive`, so the directory path itself is fully attacker-controlled via `FileHeader.Name`.

### Finding Description
`extractZipFile` unconditionally does: [1](#0-0) 
before dispatching to `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, or `extractZipFileEntry`, all of which operate directly on `file.Name` with no root-confinement/join against a fixed extraction root [2](#0-1) . The only pre-extraction check performed by `ExtractZipArchive` is `errorIfGitDirectory`, which only rejects paths starting with `.git` [3](#0-2) ; there is no rejection of absolute paths or `..` segments, and no clamping of the `0o777` mode passed to `MkdirAll`.

Given the stated precondition (extraction running with a permissive umask in a shared helper container), `MkdirAll(dir, 0o777)` will materialize intermediate directories with permissions up to world-writable, since Go's `os.Mkdir`/`MkdirAll` apply `mode &^ umask` — the code does nothing to floor or clamp this beyond what the OS umask does. This is a real, unguarded pattern: the mode constant is not derived from any Runner policy (e.g., a fixed `0o700`/`0o750`) and is not intersected with the extraction root's own permissions.

### Impact Explanation
If the helper's umask is permissive (as stipulated by the question's precondition) and the extraction root sits inside a filesystem region shared by multiple jobs/tenants, intermediate directories created by an attacker-crafted archive path (e.g., `a/b/c/file`) could be created world-writable, and — because `MkdirAll` is a no-op for a directory that already exists regardless of its recorded mode — a job could also pre-create a directory that a *later* job's extraction step will silently reuse without tightening permissions. This is a permission-widening issue confined to the directory tree(s) actually created during extraction; it does not by itself grant escape from the executor sandbox, but it can leave residual world-writable directories on a filesystem that is reused/shared across job runs (e.g., persistent cache/build volumes, or shared helper containers where umask is not enforced).

### Likelihood Explanation
Reachability is straightforward: cache/artifact zip extraction (`commands/helpers/cache_extractor.go` → `archive.NewExtractor` → `ziplegacy.extractor.Extract` → `archives.ExtractZipArchive` → `extractZipFile`) is fully driven by attacker-supplied archive content (a job can produce/upload an artifact or cache zip with arbitrary entry names) [4](#0-3) [5](#0-4) . The bug's actual severity is gated by the container/process umask, which the question sets as a precondition rather than something demonstrated in-repo; on typical Linux defaults (umask `022`), directories created via `0o777` become `0o755`, not world-writable, so the concrete "world-writable" outcome requires an unusually permissive umask (e.g., `0` or `002` combined with unusual shared-group setups) that is not something this codebase configures or guarantees one way or the other.

### Recommendation
Clamp directory-creation mode independent of umask assumptions — e.g., `os.MkdirAll(filepath.Dir(file.Name), 0o750)` and consider `syscall.Umask`-independent enforcement (explicit `os.Chmod` after creation to intersect with a fixed maximum, e.g., `0o750`), plus reject archive entries whose `Name` is absolute or contains `..` path segments (the current `errorIfGitDirectory` check is not a general path-traversal guard) before calling `MkdirAll`/`OpenFile`/`Symlink`.

### Proof of Concept
```go
func TestExtractZipFile_DirModeNotWorldWritable(t *testing.T) {
    origUmask := syscall.Umask(0) // simulate permissive umask precondition
    defer syscall.Umask(origUmask)

    tmpDir := t.TempDir()
    wd, _ := os.Getwd()
    require.NoError(t, os.Chdir(tmpDir))
    defer os.Chdir(wd)

    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    f, _ := zw.Create("a/b/c/file")
    io.WriteString(f, "x")
    zw.Close()

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)
    require.NoError(t, ExtractZipArchive(zr))

    for _, dir := range []string{"a", "a/b", "a/b/c"} {
        st, err := os.Stat(dir)
        require.NoError(t, err)
        perm := st.Mode().Perm()
        assert.NotEqual(t, os.FileMode(0o777), perm, "directory %s should not be world-writable", dir)
    }
}
```
This test currently would show the created directories end up with exactly `0o777` permissions when the umask is `0`, demonstrating the lack of an independent floor/clamp in `extractZipFile`. The scoped, cross-tenant impact (another job/tenant reading/writing these directories) is only realized under the stated precondition of a shared filesystem plus a permissive umask, which is outside the direct control of this code path.

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

**File:** helpers/archives/zip_extract.go (L61-66)
```go
func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
	}
```

**File:** helpers/archives/path_check_helper.go (L13-31)
```go
func isPathAGitDirectory(path string) bool {
	parts := strings.Split(filepath.Clean(path), string(filepath.Separator))
	if len(parts) > 0 && parts[0] == ".git" {
		return true
	}
	return false
}

func errorIfGitDirectory(path string) *os.PathError {
	if !isPathAGitDirectory(path) {
		return nil
	}

	return &os.PathError{
		Op:   ".git inside of archive",
		Path: path,
		Err:  errors.New("trying to archive or extract .git path"),
	}
}
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
