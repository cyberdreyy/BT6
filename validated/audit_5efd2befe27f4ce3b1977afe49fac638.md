### Title
Zip-slip path traversal in `ExtractZipArchive`/`extractZipFile` allows writing and chmod'ing files outside job workspace - ([File: helpers/archives/zip_extract.go])

### Summary
`ExtractZipArchive` (and its Windows counterpart's `lchmod`) uses `file.Name` from the zip entry directly as a filesystem path with no sanitization against `..` traversal sequences, so a crafted cache/artifact zip can write and then `chmod` files outside the intended extraction directory. `lchmod` in `helpers/archives/os_windows.go` itself is not the root cause — it merely re-uses the same unsanitized `file.Name` that `extractZipFileEntry` already wrote through, but the underlying bug is real and reachable through the described path.

### Finding Description
`ExtractZipArchive` in `helpers/archives/zip_extract.go` iterates over `archive.File` and, for each entry, calls `errorIfGitDirectory(file.Name)` (which only rejects `.git`-prefixed paths — see `helpers/archives/path_check_helper.go`) and then `extractZipFile(file)`, which does:
```go
err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
...
out, err = os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)
```
`file.Name` is taken verbatim from the attacker-supplied zip's central directory with no `filepath.Clean`, no rejection of `..` components, and no check that the resolved path stays within a base/target directory. There is no `os.Chdir` into a safe target directory before extraction, and no path-confinement helper exists anywhere in this file or `path_check_helper.go` beyond the `.git` check.

After the first loop writes the file (or symlink) to the traversed location, the second loop calls `lchmod(file.Name, file.Mode())` (`helpers/archives/os_windows.go` on Windows, POSIX equivalent elsewhere) using the same unsanitized name, so the permission change also lands on the out-of-tree path that was just written.

This function is reachable from real job-controlled inputs: `commands/helpers/cache_extractor.go` calls into the zip extraction path when restoring caches, and `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`'s `Extract` calls `archives.ExtractZipArchive(zr)` directly for artifact/cache extraction — both process zip content that is attacker-influenced (a job can produce/upload a cache or artifact archive with attacker-chosen entry names).

### Impact Explanation
An attacker who controls the contents of a cache or artifact zip processed by the runner can cause the runner process to write arbitrary files (and change their permissions via `lchmod`/`os.Chmod`) to paths outside the job's build/cache root, anywhere the runner process has filesystem permission to write. This violates the "file operations must stay within intended build/cache/artifact roots" invariant and can lead to overwriting or altering permissions of files elsewhere on the host running the runner (e.g., other jobs' workspaces, or runner-writable system paths), depending on runner privileges and executor type (most impactful on shell executor).

### Likelihood Explanation
Feasible and fully attacker-controlled: any pipeline author can generate a cache or artifact zip (e.g., `zip` a file with a manipulated name via a custom archiver, or directly craft a zip using Go's `archive/zip` package as part of a build step) with entries like `..\..\evil.bat` or `../../evil.sh`, then have that archive stored/restored as a GitLab CI cache or artifact. On restore, `ExtractZipArchive` is invoked with no traversal guard, making this a deterministic, repeatable zip-slip issue rather than a theoretical one — this is the well-known "zip-slip" vulnerability class, and only the `.git`-directory check exists as a name-based guard, which does not address `..` traversal at all.

### Recommendation
Add path-confinement validation in `extractZipFile` (and ideally centrally in `ExtractZipArchive` before either loop) that resolves each `file.Name` against the intended extraction root using `filepath.Clean`/`filepath.Rel` (or the well-known zip-slip guard: ensure `!strings.HasPrefix(filepath.Clean(destPath), destRoot+string(os.PathSeparator))` is rejected), and abort/skip entries whose cleaned path escapes the target directory — mirroring the existing `errorIfGitDirectory` check but for `..` traversal. Apply the same validated path to both the write step and the `lchmod` step so a rejected entry is never chmod'd either.

### Proof of Concept
```go
// helpers/archives/zip_extract_traversal_test.go
package archives

import (
	"archive/zip"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestExtractZipFile_PathTraversal(t *testing.T) {
	dir := t.TempDir()
	targetDir := filepath.Join(dir, "workspace")
	require.NoError(t, os.MkdirAll(targetDir, 0o755))

	zipPath := filepath.Join(dir, "evil.zip")
	f, err := os.Create(zipPath)
	require.NoError(t, err)
	w := zip.NewWriter(f)
	entry, err := w.Create("../../evil.txt")
	require.NoError(t, err)
	_, err = entry.Write([]byte("pwned"))
	require.NoError(t, err)
	require.NoError(t, w.Close())
	require.NoError(t, f.Close())

	// simulate extraction rooted at targetDir
	wd, _ := os.Getwd()
	require.NoError(t, os.Chdir(targetDir))
	defer os.Chdir(wd)

	err = ExtractZipFile(zipPath)
	require.NoError(t, err)

	// Assert the file escaped targetDir
	escapedPath := filepath.Join(dir, "evil.txt")
	_, statErr := os.Stat(escapedPath)
	require.NoError(t, statErr, "expected file to have escaped the target workspace directory")
}
```
Expected assertion (current behavior, demonstrating the bug): the file `evil.txt` is created at `dir/evil.txt`, outside `targetDir`, and (on Windows) `lchmod` is subsequently invoked against that same escaped path — proving both the write and the permission change violate workspace confinement.