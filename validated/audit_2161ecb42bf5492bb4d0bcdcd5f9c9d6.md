### Title
Tar/zstd cache & artifact extraction follows pre-existing symlinks in intermediate path components, allowing writes outside the build root - ([File: commands/helpers/archive/tarzstd/tarzstd_extractor.go])

### Summary
`extractor.Extract` in `commands/helpers/archive/tarzstd/tarzstd_extractor.go` validates each archive entry's *nominal* destination path with a string-prefix check, but never verifies that intermediate path components are real directories rather than symlinks planted in the workspace beforehand. Because `os.MkdirAll` and `os.Create` transparently follow existing symlinks, an attacker who can get a symlink checked out into the job workspace before cache/artifact restoration (e.g. by committing a symlink into their own git repository) can redirect extracted cache/artifact file writes to an arbitrary location outside the build root.

### Finding Description
`CacheExtractorCommand.Execute` (`commands/helpers/cache_extractor.go:618-664`) opens the downloaded cache archive and calls `archive.NewExtractor(format, f, size, wd).Extract(ctx)` with `wd` (the job's current working directory) as the extraction root [1](#0-0) . For the tar+zstd format, `Extract` computes each entry's absolute path and only checks that the resulting string has `e.dir` as a prefix: [2](#0-1) 

This check only rejects `../`-style traversal encoded in the tar header name itself. It does not `Lstat` each intermediate path component to confirm none of them is a symlink. For regular files, the code does: [3](#0-2) 

`os.MkdirAll(filepath.Dir(path), 0777)` (line 66) and `os.Create(path)` both resolve symlinks transparently at the OS level: if `filepath.Dir(path)` (e.g. `<wd>/some-dir`) already exists as a symlink to `/etc/cron.d`, `MkdirAll` sees an existing directory (via the symlink) and does nothing, and `os.Create` then writes the actual file through the symlink into `/etc/cron.d`.

The attacker-controlled precondition is trivial to satisfy: a normal GitLab job's own `git checkout`/`fetch` stage (which runs before cache restoration in the standard job stage order) can check out a symlink object committed by the pipeline author into their own repository, e.g. `some-dir -> /etc/cron.d`. On a later job run that restores a cache containing an entry `some-dir/payload`, the tar extractor writes `payload` through the pre-existing symlink to a location outside the job's build root.

`cache/cache.go`'s `GetAdapter` path-traversal guard (the `strings.HasPrefix(fullPath, basePath+"/")` check referenced in the question) is unrelated to this bug — it governs the remote object-storage key naming and has no bearing on how the downloaded archive is later extracted on the local filesystem, so it neither causes nor prevents this issue.

### Impact Explanation
An unprivileged pipeline author can achieve arbitrary file write outside the intended build/cache root on the job's execution host (or container filesystem, depending on executor), by combining a repository-checked-in symlink with a self-controlled cache (the attacker fully controls their own project's cache key/contents). This breaks the "file operations must stay within intended build/cache/artifact roots" invariant and, on shell/host-mounted executors, can lead to writing files to arbitrary host paths reachable by the runner process (e.g., cron directories, other mounted paths).

### Likelihood Explanation
Fully attacker-controlled and repeatable: the attacker controls both the git repository content (the symlink) and the cache contents/paths for their own project, requiring no elevated permission beyond running a normal CI job. No race condition or timing dependency is needed since git clone reliably precedes cache restore in the job stage sequence.

### Recommendation
In `tarzstd_extractor.go`'s `Extract`, before creating/writing each entry, resolve and validate every path component against symlink substitution: e.g., use `filepath.EvalSymlinks` on `filepath.Dir(path)` (or walk components with `os.Lstat`, rejecting/removing any symlink not created by the current extraction) and confirm the resolved directory is still within `e.dir` before calling `os.MkdirAll`/`os.Create`. Alternatively, refuse to `MkdirAll` through an existing symlink (`Lstat` each already-existing path segment and error out if `Mode()&os.ModeSymlink != 0`), matching the deferred-symlink handling already used for archive-declared symlinks later in the same function.

### Proof of Concept
Go test plan for `tarzstd` package:
1. Create a temp extraction root `dir`.
2. Inside `dir`, create `os.Symlink(outsideDir, filepath.Join(dir, "some-dir"))` where `outsideDir` is a separate temp directory simulating `/etc`.
3. Build an in-memory tar+zstd archive containing a single regular file entry `some-dir/payload` with attacker content.
4. Call `NewExtractor(reader, size, dir).Extract(ctx)`.
5. Assert: `payload` must NOT exist inside `outsideDir` (i.e., extraction must fail or write only within `dir`); currently the test would show `outsideDir/payload` exists, proving the escape.

### Citations

**File:** commands/helpers/cache_extractor.go (L655-663)
```go
	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L57-68)
```go
		var path string
		path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
			return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
		}

		if err := os.MkdirAll(filepath.Dir(path), 0777); err != nil {
			return err
		}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L87-99)
```go
		case fi.Mode().IsRegular():
			f, err := os.Create(path)
			if err != nil {
				return err
			}

			if _, err := io.Copy(f, tr); err != nil {
				f.Close()
				return err
			}
			if err := f.Close(); err != nil {
				return err
			}
```
