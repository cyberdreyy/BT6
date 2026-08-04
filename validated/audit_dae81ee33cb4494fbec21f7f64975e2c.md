### Title
`extractZipSymlinkEntry`/`createZipSymlinkEntry` round-trip stores and recreates unvalidated symlink targets, allowing symlinks that escape the extraction root - ([File: helpers/archives/zip_create.go])

### Summary
`createZipSymlinkEntry` archives the raw output of `os.Readlink(fh.Name)` with no validation, and the matching `extractZipSymlinkEntry` in `helpers/archives/zip_extract.go` recreates the symlink with `os.Symlink(string(data), file.Name)` using that raw value verbatim. Neither side checks whether the link target is absolute or contains `../` segments, so a symlink an attacker plants in the job's working tree before artifact/cache archiving will be faithfully reproduced — pointing wherever the attacker chose — on any host that later extracts the archive.

### Finding Description
`createZipSymlinkEntry` (`helpers/archives/zip_create.go:17-30`) writes the symlink target into the zip entry via [1](#0-0) . It never inspects or canonicalizes `link` — an absolute path (`/etc/passwd`), a relative traversal (`../../../../etc`), or a Windows UNC-like string is stored byte-for-byte.

On the extraction side, `extractZipSymlinkEntry` (`helpers/archives/zip_extract.go:22-39`) reads that stored value back and calls `os.Symlink(string(data), file.Name)` with no check that the target stays inside the destination root [2](#0-1) . The only path confinement present anywhere in `ExtractZipArchive` (`helpers/archives/zip_extract.go:85-110`) is the git-directory warning tracker (`errorIfGitDirectory`) — there is no `filepath.Clean`/`IsAbs`/prefix check on the entry name and none whatsoever on the symlink target. Compare this to `commands/helpers/archive/tarzstd/tarzstd_extractor.go`, which validates that the *extracted file path* stays inside `e.dir` (lines 58-64) but still calls `os.Symlink(hdr.Linkname, path)` unchecked (line 114) — the same class of gap exists in the tar+zstd path too, confirming this isn't a one-off oversight but a genuine missing invariant in this codebase's archive/extract layer.

An attacker who is just a normal pipeline author can create such a symlink in a `before_script`/`script` step (e.g. `ln -s /etc ci-escape` or `ln -s ../../../../ artifacts/escape`) inside the paths listed in `artifacts:paths` or `cache:paths`. The archiver walks the working directory, calls `os.Lstat`, detects `os.ModeSymlink`, and archives it via `createZipSymlinkEntry` without any rejection.

### Impact Explanation
The concrete effect is that `ExtractZipArchive` can recreate a symlink at a predictable path inside the extraction directory whose target is fully attacker-chosen (absolute or `..`-escaping). This breaks the "file operations must stay within intended build/cache/artifact roots" invariant: any later runner-side or user-side operation that follows that path (a subsequent job step that reads/writes through it, a cache restore that treats it as a normal file, or another archiving pass that walks into it) can read or write data outside the extraction root, on whichever machine performs the extraction (the same runner for cache pull, or a different runner/job for artifact download). It does not by itself grant execution or file read of arbitrary system files without that dangling symlink later being dereferenced, but it plants a persistent, cross-job escape primitive.

### Likelihood Explanation
Fully attacker-controlled and requires no special privileges — any job author can run `ln -s <target> <path>` before the artifact-uploader or cache-archiver runs, and both simply call `CreateZipArchive` → `os.Lstat` → `createZipSymlinkEntry` on whatever the job left in the working tree. The condition is 100% reproducible: create the symlink, run `zip_create`/`zip_extract` (or the actual `artifacts-uploader`/`cache-archiver` and `artifacts-downloader`/`cache-extractor` commands), and observe the symlink recreated with the same escaping target.

### Recommendation
In `extractZipSymlinkEntry` (and the analogous tar path), resolve the intended destination via `filepath.Join(extractionRoot, file.Name)` and reject/neutralize link targets that are absolute or whose cleaned, joined path (`filepath.Join(filepath.Dir(destPath), target)`) escapes `extractionRoot` (i.e., `!strings.HasPrefix(rel, "..")` check as already used elsewhere in this codebase, e.g. `AGENTS.md` guidance on `filepath.Rel` validation). Apply the same validation defensively in `createZipSymlinkEntry` so malicious working-tree symlinks are not silently archived either.

### Proof of Concept
```go
func TestZipSymlinkEscape(t *testing.T) {
    dir := t.TempDir()
    wd, _ := os.Getwd()
    defer os.Chdir(wd)
    os.Chdir(dir)

    // attacker-controlled symlink with escaping/absolute target
    require.NoError(t, os.Symlink("/etc/passwd", "escape-link"))

    var buf bytes.Buffer
    require.NoError(t, CreateZipArchive(&buf, []string{"escape-link"}))

    extractDir := t.TempDir()
    os.Chdir(extractDir)
    r, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)
    require.NoError(t, ExtractZipArchive(r))

    target, err := os.Readlink(filepath.Join(extractDir, "escape-link"))
    require.NoError(t, err)
    // Expected (secure) behavior: target should be rejected or confined
    assert.False(t, filepath.IsAbs(target), "symlink target escaped extraction root: %s", target)
}
```
This test currently fails against the given code (the symlink is recreated pointing at `/etc/passwd`), demonstrating the missing confinement check.

### Citations

**File:** helpers/archives/zip_create.go (L23-29)
```go
	link, err := os.Readlink(fh.Name)
	if err != nil {
		return err
	}

	_, err = io.WriteString(fw, link)
	return err
```

**File:** helpers/archives/zip_extract.go (L35-38)
```go
	// Remove symlink before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	err = os.Symlink(string(data), file.Name)
	return
```
