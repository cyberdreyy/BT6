### Title
Unvalidated `hdr.Linkname` in tar.zst extraction allows creation of symlinks pointing outside the job's extraction root - ([File: commands/helpers/archive/tarzstd/tarzstd_extractor.go])

### Summary
`extractor.Extract` validates that a symlink entry's own destination *path* stays inside `e.dir` (`filepath.Abs`/`strings.HasPrefix` check), but never validates `hdr.Linkname`, the string used as the symlink's target, before calling `os.Symlink(hdr.Linkname, path)`. A user-controlled `tar.zst` cache/artifact archive can therefore contain a symlink whose name resolves inside `e.dir` (passing the existing check) but whose target is an arbitrary absolute or relative path reaching outside `e.dir`, including sibling paths in a shared `builds_dir`/cache volume.

### Finding Description
In `commands/helpers/archive/tarzstd/tarzstd_extractor.go`, `Extract` computes `path` from `hdr.Name` and checks it against `e.dir`: [1](#0-0) 

For symlink entries (`fi.Mode()&os.ModeSymlink != 0`), the header is deferred and later materialized with: [2](#0-1) 

`hdr.Linkname` is passed to `os.Symlink` completely unchecked - there is no `filepath.Abs`/prefix validation, no rejection of absolute paths, and no rejection of `..`-containing relative targets. This is the classic "tar symlink" escape pattern: the containment check only guards *where the link is created*, not *what it points to*. An attacker fully controls `hdr.Linkname` via a crafted cache/artifact archive uploaded by `CacheArchiverCommand` (or a hand-crafted `tar.zst` payload, since cache/artifact download-and-extract accepts attacker-influenced archive contents by design - the archive contents come from the job's own build/cache, but in shared/persistent-storage configurations that data outlives and is later read by other job runs).

Given GitLab Runner's own documentation, `builds_dir`/`cache_dir` layouts are namespaced (`<runner>/<concurrency-id>/<namespace>/<project>` for shared dirs, `<cache-dir>/<runner-id-hash>/<md5-of-path>` for cache volumes) but multiple projects/concurrent slots frequently share the *same root volume* (host bind mount, PVC, `ReadWriteMany`, docker-machine autoscaler cache volumes), as shown in `common/build.go`'s `ProjectUniqueDir` and the Kubernetes/Docker persistent-storage docs. A dangling symlink left inside a job's own subdirectory of that shared root, with `Linkname` set to an absolute path such as `/builds/other-namespace/other-project/...` or a relative `../../other-project/...`, is visible to any process with access to that mounted volume - which can include another project's helper/build container if the same volume/PVC/host directory is reused across projects (a documented, supported configuration, not merely an "admin misconfiguration" like `privileged` mode).

### Impact Explanation
Because the deferred symlink is created without any validation of its target, a job can leave behind, inside its own (properly namespaced) build/cache subtree, a symlink that resolves to a path elsewhere on the shared volume. If that shared volume backs multiple projects (per documented shared-builds-dir / shared-cache-volume configurations) and the runner or a later job process (e.g. cache extractor of another job, or a `find`/script step of a later job that indiscriminately walks the shared root) dereferences it, this results in cross-job/cross-project file read or write via the dangling symlink - matching the invariant "File operations must stay within intended build/cache/artifact roots." Even absent cross-project sharing, it is at minimum a symlink-escape primitive: a job can plant a symlink at a path inside its own `e.dir` that points outside `e.dir` to any path visible in the extracting process's mount namespace, undermining the very purpose of the containment check that exists for entry *names* but is absent for link *targets*.

### Likelihood Explanation
The archive contents (tar headers, including `Linkname`) are entirely attacker-controlled - any pipeline author can produce a custom `tar.zst` cache/artifact via a job's `cache`/`artifacts` config (or by crafting a payload and pushing it as a cache blob). No special privilege beyond the ability to run a pipeline is required. The bug is triggered on every `CacheExtractorCommand`/artifact download that runs `tarzstd.NewExtractor(...).Extract()`. Full cross-project impact additionally requires a shared builds/cache volume topology (explicitly documented and supported for Kubernetes PVC/`ReadWriteMany` and Docker host-bound persistent storage), which is a realistic, non-default-but-supported deployment, not an inherently insecure admin choice.

### Recommendation
Validate `hdr.Linkname` the same way `path` is validated before calling `os.Symlink`:
- Reject entries where `hdr.Linkname` is an absolute path.
- Resolve relative `Linkname` against the symlink's containing directory (`filepath.Join(filepath.Dir(path), hdr.Linkname)`), then apply the same `filepath.Abs` + `strings.HasPrefix(resolved, e.dir+separator)` check used for `path`, rejecting the entry (or skipping symlink creation) if it escapes `e.dir`.
- Apply the identical fix pattern to any other archive extractors in `commands/helpers/archive/` that create symlinks from tar headers (currently only the `tarzstd` package handles `tar.TypeSymlink`/`os.ModeSymlink`, per repository search - `fastzip`/`ziplegacy` extractors showed no `Linkname` handling matches, but should still be checked for equivalent zip symlink support).

### Proof of Concept
Go unit test to add near `tarzstd_extractor.go` (e.g. `tarzstd_extractor_test.go`):
```go
func TestExtract_RejectsSymlinkEscapingViaLinkname(t *testing.T) {
    dir := t.TempDir()

    var buf bytes.Buffer
    zw, _ := zstd.NewWriter(&buf)
    tw := tar.NewWriter(zw)

    // symlink NAME is inside dir (passes existing path check),
    // but LINKNAME points outside dir.
    hdr := &tar.Header{
        Name:     "evil-link",
        Typeflag: tar.TypeSymlink,
        Linkname: "/etc/passwd", // or "../../other-project/secret"
        Mode:     int64(os.ModeSymlink | 0777),
    }
    require.NoError(t, tw.WriteHeader(hdr))
    require.NoError(t, tw.Close())
    require.NoError(t, zw.Close())

    r := bytes.NewReader(buf.Bytes())
    e, err := NewExtractor(r, int64(r.Len()), dir)
    require.NoError(t, err)

    err = e.Extract(context.Background())

    // Expected (fixed) behavior: extraction should fail or refuse to
    // create a symlink whose target escapes `dir`.
    require.Error(t, err)

    linkPath := filepath.Join(dir, "evil-link")
    if target, lerr := os.Readlink(linkPath); lerr == nil {
        require.False(t, filepath.IsAbs(target) || strings.HasPrefix(target, ".."),
            "symlink target must not escape extraction root")
    }
}
```
Current behavior: `Extract` returns `nil` and `os.Readlink(dir+"/evil-link")` returns `/etc/passwd`, demonstrating the unchecked `Linkname` is written verbatim by `os.Symlink(hdr.Linkname, path)`.

### Citations

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

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L107-117)
```go
	for path, hdr := range deferred {
		fi := hdr.FileInfo()
		if fi.Mode()&os.ModeSymlink == 0 && !fi.Mode().IsDir() {
			continue
		}

		if fi.Mode()&os.ModeSymlink != 0 {
			if err := os.Symlink(hdr.Linkname, path); err != nil {
				return err
			}
		}
```
