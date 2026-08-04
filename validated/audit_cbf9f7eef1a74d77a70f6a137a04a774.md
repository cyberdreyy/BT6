### Title
Unsanitized zip entry names allow directory-traversal writes via `os.MkdirAll` in `extractZipFile` - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile` calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` using the raw, attacker-controlled `zip.File.Name` with no path sanitization or containment check. A crafted artifact/cache zip with `../` sequences in entry names causes directory creation (and, if not for later write failure, full file write) outside the intended extraction root.

### Finding Description
`ExtractZipArchive` iterates over `archive.File` and, for each entry, only checks `errorIfGitDirectory(file.Name)` [1](#0-0)  before calling `extractZipFile`. That function immediately does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` on the unmodified `file.Name` [2](#0-1) , and subsequently `extractZipFileEntry`/`extractZipSymlinkEntry` write to `file.Name` directly as well [3](#0-2) [4](#0-3) .

The only existing guard, `errorIfGitDirectory`, checks solely whether the first path component (after `filepath.Clean`) is `.git`; it performs no traversal detection at all [5](#0-4) . There is no `filepath.Clean`/`filepath.Rel`/prefix-containment check anywhere in this package to constrain `file.Name` to the destination root — confirmed by the absence of any such sanitization logic across `helpers/archives/*.go`. Go's `archive/zip` package itself does not sanitize `File.Name` for callers using the raw `zip.Reader` API (that protection only applies to the `fs.FS`-based `Open` method, which is not used here).

Consequently, a zip entry named e.g. `../../../../tmp/evil/marker` is passed straight to `filepath.Dir`, which resolves to `../../../../tmp/evil`, and `os.MkdirAll` will create that full directory chain outside the job's working directory/extraction root, regardless of whether the subsequent file write succeeds.

### Impact Explanation
An unprivileged pipeline author who controls artifact/cache zip content (e.g., via `artifacts:` upload from a job, or a crafted cache archive consumed by another job on the same runner/host in shell or similar non-isolated executors) can cause the runner process to create arbitrary directories anywhere the runner process has filesystem permissions, outside the intended job workspace. This violates the invariant that archive-driven filesystem mutations remain confined to the job root. In shell/non-containerized executors, this can pollute or create directories under paths the runner user can write to.

### Likelihood Explanation
High feasibility: it requires only a job author's ability to produce/upload an artifact or cache zip with crafted entry names, which is a completely attacker-controlled input already accepted and processed by `ExtractZipArchive`/`ExtractZipFile`. No authentication bypass or admin action is required — it is directly reachable through the normal artifact/cache download-and-extract flow. It is fully repeatable (deterministic `MkdirAll` behavior).

### Recommendation
Before calling `os.MkdirAll`/opening files, sanitize each `file.Name`: reject or clean entries whose cleaned path escapes the destination root (e.g., compute `target := filepath.Join(destRoot, file.Name)` then verify `filepath.Rel(destRoot, target)` does not start with `..` or equivalent, similar to standard "zip slip" mitigations), and reject the entry outright if it does.

### Proof of Concept
Go unit test:
```go
func TestExtractZipFile_PathTraversal(t *testing.T) {
    sandbox := t.TempDir()
    // build an in-memory zip with an entry "../../evil/marker"
    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    w, _ := zw.Create("../../evil/marker")
    _, _ = w.Write([]byte("x"))
    zw.Close()

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))

    // chdir into sandbox to simulate the extraction root
    oldWd, _ := os.Getwd()
    _ = os.Chdir(sandbox)
    defer os.Chdir(oldWd)

    _ = ExtractZipArchive(zr)

    escaped := filepath.Join(filepath.Dir(sandbox), "evil")
    _, err := os.Stat(escaped)
    assert.NoError(t, err, "directory was created outside sandbox root: zip slip via MkdirAll")
}
```
Expected assertion: the test should fail today (directory `evil` is created outside `sandbox`), demonstrating the traversal; after adding path containment checks, `os.Stat(escaped)` should return `os.ErrNotExist`.

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

**File:** helpers/archives/zip_extract.go (L61-66)
```go
func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
	}
```

**File:** helpers/archives/zip_extract.go (L88-95)
```go
	for _, file := range archive.File {
		if err := errorIfGitDirectory(file.Name); tracker.actionable(err) {
			printGitArchiveWarning("extract")
		}

		if err := extractZipFile(file); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
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
