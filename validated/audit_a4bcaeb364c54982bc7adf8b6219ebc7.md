### Title
Symlink target string is stored/recreated by `createZipSymlinkEntry`/`extractZipSymlinkEntry` without validation, allowing artifact/cache symlinks to point outside the job root - (File: helpers/archives/zip_create.go, helpers/archives/zip_extract.go)

### Summary
`createZipSymlinkEntry` archives whatever `os.Readlink` returns for a symlink verbatim, with no check that the target stays within the job workspace, and `extractZipSymlinkEntry` later recreates that exact string via `os.Symlink(string(data), file.Name)` with no canonicalization or root confinement. An unprivileged pipeline author can therefore create an artifact/cache entry that is a symlink pointing to an absolute path (e.g. `/var/run/secrets/kubernetes.io/serviceaccount/token`) or a `../../` traversal, and have it faithfully re-created wherever the archive is later extracted.

### Finding Description
`createZipEntry` in `helpers/archives/zip_create.go` dispatches on `os.ModeSymlink` to `createZipSymlinkEntry`: [1](#0-0) 
This writes the raw `os.Readlink(fh.Name)` output as the zip entry's payload with zero validation - no check for `filepath.IsAbs`, no check for `..` segments, no restriction to the job/build directory.

On extraction, `extractZipFile` dispatches symlink entries to `extractZipSymlinkEntry` in `helpers/archives/zip_extract.go`: [2](#0-1) 
It reads the stored bytes and passes them straight into `os.Symlink(string(data), file.Name)`, again with no validation of the target. The only path-safety check present anywhere in the package is `errorIfGitDirectory`/`isPathAGitDirectory` in `helpers/archives/path_check_helper.go`, which only guards against `.git` paths in the archived *entry name*, not the symlink *target*.

Reachable Go path: `CreateZipArchive` -> `createZipEntry` -> `createZipSymlinkEntry` (archive-time) and `ExtractZipArchive` -> `extractZipFile` -> `extractZipSymlinkEntry` (extract-time), both exported/used by the artifact/cache archiver and extractor commands (`commands/helpers/cache_archiver.go`, `commands/helpers/artifacts_uploader.go`, `commands/helpers/artifacts_downloader.go`, `commands/helpers/cache_extractor.go`).

Attacker-controlled input: a job script under attacker control can run `ln -s <arbitrary-target> foo` inside the build directory and list `foo` in an `artifacts:paths`/`cache:paths` entry. Nothing in the Runner's archiving code rejects this before or during archiving, and nothing rejects it during extraction.

### Impact Explanation
The immediate, concretely provable impact is: a symlink whose target is an arbitrary absolute path or traversal string can be smuggled through the artifact/cache zip format and faithfully recreated in the filesystem of whatever process later extracts the archive. If that extraction happens in a context (a different job/pod) where the same absolute path resolves to a more sensitive mount (e.g. a service-account token, a different service's mounted volume), any subsequent read of the recreated symlink by that job's own script/tooling will transparently follow it and disclose that content. This is a genuine "file operations must stay within intended build/cache/artifact roots" violation. However, achieving the specifically scoped "stronger identity than configured" outcome additionally requires that the consuming job actually runs with a different/more-privileged Kubernetes identity than the producing job (e.g. via `KUBERNETES_SERVICE_ACCOUNT_OVERWRITE`/namespace-overwrite features, or across pipelines with different runner tags) and that the consuming job's own script dereferences the recreated symlink (e.g. `cat`, `cp`, glob expansion) so the content is exposed to the attacker. Those extra preconditions are outside the archive code itself.

### Likelihood Explanation
The archive-side bug (missing symlink-target validation) is trivially and reliably reproducible by any pipeline author with `artifacts`/`cache` access - no special runner configuration is needed to demonstrate the round-trip. Escalating this into cross-identity secret exfiltration is feasible only when the deployment additionally allows per-job Kubernetes namespace/service-account overwrites and a downstream job (same or dependent pipeline) processes the recreated symlink; this depends on optional Runner/K8s executor configuration rather than being guaranteed by default.

### Recommendation
Reject or canonicalize symlink targets at both archive and extraction time: in `createZipSymlinkEntry`, refuse to archive symlinks whose `os.Readlink` target is absolute or, once joined with the entry's directory and `filepath.Clean`-ed, escapes the job root; in `extractZipSymlinkEntry`, apply the same validation before calling `os.Symlink`, rejecting absolute targets and targets that resolve (via `filepath.Clean`/`filepath.Rel`) outside the extraction root.

### Proof of Concept
```go
func TestZipSymlinkTargetEscape(t *testing.T) {
    dir := t.TempDir()
    link := filepath.Join(dir, "escape")
    require.NoError(t, os.Symlink("/var/run/secrets/kubernetes.io/serviceaccount/token", link))

    var buf bytes.Buffer
    require.NoError(t, CreateZipArchive(&buf, []string{link}))

    extractDir := t.TempDir()
    // simulate extraction in a different root
    r, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)
    for _, f := range r.File {
        target, _ := io.ReadAll(mustOpen(f))
        // current behavior: target is unvalidated absolute path
        assert.False(t, filepath.IsAbs(string(target)),
            "extractor must reject absolute symlink targets, got %q", target)
    }
}
```
Expected (current) result: the assertion fails, proving the stored/round-tripped target is the raw absolute path with no validation - confirming `createZipSymlinkEntry`/`extractZipSymlinkEntry` perform no confinement check, which is the concrete root cause underlying the described escalation path.

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
