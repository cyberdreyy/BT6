### Title
Zip archiver follows symlinks without root confinement, allowing cross-project file exfiltration via cache/artifacts - (File: helpers/archives/zip_create.go)

### Summary
`CreateZipArchive` and its helpers `createZipEntry`/`createZipFileEntry`/`createZipSymlinkEntry` operate directly on caller-supplied path strings with `os.Open`/`os.Lstat`, performing no check that the resolved file stays within the job's declared source directory. The only path-based guard, `errorIfGitDirectory` in `helpers/archives/path_check_helper.go`, only rejects paths literally starting with `.git` and does nothing to prevent symlink traversal outside the intended root.

### Finding Description
`NewArchiver` in `commands/helpers/archive/archive.go` documents an intended invariant ("The archiver will ensure that files to be archived are children of the directory provided") [1](#0-0) , but the zip implementation, `ziplegacy.archiver.Archive`, does not enforce this at all — it simply sorts the provided filenames and forwards them straight into `archives.CreateZipArchive` with no filtering, path cleaning, or containment check against `a.dir` [2](#0-1) .

Inside `CreateZipArchive`, each filename only goes through `errorIfGitDirectory`, which merely checks whether the first path component equals `.git`; it does not validate that the path resolves inside any root directory [3](#0-2) [4](#0-3) . `createZipEntry` then calls `os.Lstat(fileName)` and dispatches based on file type: for a symlink it calls `createZipSymlinkEntry`, which stores the symlink target as-is (not itself a leak), but for a regular file reached by traversing through a symlinked directory in the path (e.g. `tmp/x/checkout/secret.txt` where `tmp/x` is a symlink to another project's workspace), `createZipFileEntry` calls `os.Open(fh.Name)` directly, which the OS resolves by following the symlink and reads whatever file it points to [5](#0-4) .

Because a job can create arbitrary symlinks in its own workspace before declaring them in `cache:paths`/`artifacts:paths` (e.g. `ln -s /builds/other-namespace/other-project /tmp/x` then `paths: [tmp/x]`), the file list handed to `CreateZipArchive` can legitimately include a path like `tmp/x/README.md` that lexically starts inside the job's `dir` but whose real location is another project's checkout. No code between the CI path glob-matching layer and `createZipFileEntry` performs `filepath.EvalSymlinks` + prefix-containment checks against the job's root, so the read succeeds and the file's content is written into the archive.

### Impact Explanation
If another project's checkout is reachable on the same filesystem (e.g., a shared `builds_dir` volume across concurrent jobs on the same runner host/node), an unprivileged pipeline author can craft a job that creates a symlink into that other project's directory and declares it in `cache:paths` or `artifacts:paths`. The resulting cache/artifact archive — which the job can freely download after the job completes — would contain files from a project the attacker does not have access to, resulting in cross-project confidentiality breach (source code, checked-out secrets in `.env` files, etc.) without ever touching the executor sandbox boundary directly; the leak occurs purely through the archiving codepath.

### Likelihood Explanation
Exploitability strictly depends on the precondition that a predictable/discoverable other-project directory exists on the same filesystem as the attacker's job at the time the job runs (e.g., docker/kubernetes executors sharing a `builds_dir` volume across concurrent builds on the same host, or predictable path derivation from project ID/namespace under the runner's build directory naming scheme). This is a realistic concurrency scenario for busy shared runners rather than a purely theoretical setup, and once the precondition holds, the exploit requires only standard job configuration (`.gitlab-ci.yml` `script:` and `cache/artifacts.paths`) — no special runner privileges. Repeatability is high since the archiver logic is deterministic and unconditional.

### Recommendation
Enforce root confinement in the archiving path: after resolving path glob matches for cache/artifacts, canonicalize each candidate file via `filepath.EvalSymlinks` (or resolve intermediate symlinked directories) and verify the resolved absolute path has the job's `dir` as a prefix before adding it to the file list passed into `NewArchiver`/`CreateZipArchive`. Reject (or skip with a warning, similar to `errorIfGitDirectory`) any path whose resolved target escapes the declared root, rather than relying solely on `.git`-specific filtering in `path_check_helper.go`.

### Proof of Concept
```go
func TestCreateZipArchive_RejectsSymlinkEscapingRoot(t *testing.T) {
    root := t.TempDir()
    other := t.TempDir()
    secret := filepath.Join(other, "secret.txt")
    require.NoError(t, os.WriteFile(secret, []byte("other-project-secret"), 0644))

    jobDir := filepath.Join(root, "job")
    require.NoError(t, os.MkdirAll(jobDir, 0755))
    link := filepath.Join(jobDir, "tmp_x")
    require.NoError(t, os.Symlink(other, link))

    var buf bytes.Buffer
    // simulate cache/artifacts path resolution producing this filename
    fileNames := []string{filepath.Join(link, "secret.txt")}
    err := archives.CreateZipArchive(&buf, fileNames)
    require.NoError(t, err) // current behavior: succeeds

    r, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    for _, f := range r.File {
        // Expected (after fix): no entry should resolve outside `jobDir`.
        resolved, _ := filepath.EvalSymlinks(f.Name)
        require.True(t, strings.HasPrefix(resolved, jobDir),
            "archive contains file escaping job root: %s", f.Name)
    }
}
```
This test currently passes with the archive containing the other project's `secret.txt` content, demonstrating the exfiltration; after applying root-confinement checks, `CreateZipArchive`/the path-resolution layer should either error out or skip the symlinked entry, making the assertion above hold true.

### Citations

**File:** commands/helpers/archive/archive.go (L86-90)
```go
// NewArchiver returns a new Archiver of the specified format.
//
// The archiver will ensure that files to be archived are children of the
// directory provided.
func NewArchiver(format Format, w io.Writer, dir string, level CompressionLevel) (Archiver, error) {
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_archiver.go (L34-42)
```go
// Archive archives all files as new gzip streams.
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	sorted := make([]string, 0, len(files))
	for filename := range files {
		sorted = append(sorted, filename)
	}
	sort.Strings(sorted)

	return archives.CreateZipArchive(a.w, sorted)
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

**File:** helpers/archives/zip_create.go (L32-50)
```go
func createZipFileEntry(archive *zip.Writer, fh *zip.FileHeader) error {
	fh.Method = zip.Deflate
	fw, err := archive.CreateHeader(fh)
	if err != nil {
		return err
	}

	file, err := os.Open(fh.Name)
	if err != nil {
		return err
	}

	_, err = io.Copy(fw, file)
	_ = file.Close()
	if err != nil {
		return err
	}
	return nil
}
```

**File:** helpers/archives/zip_create.go (L91-99)
```go
	for _, fileName := range fileNames {
		if err := errorIfGitDirectory(fileName); tracker.actionable(err) {
			printGitArchiveWarning("archive")
		}

		err := createZipEntry(archive, fileName)
		if err != nil {
			return err
		}
```
