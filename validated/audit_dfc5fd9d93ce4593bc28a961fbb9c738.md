### Title
UNC extended-length share-root path (`\\?\UNC\server\share`) bypasses `windowsPath.IsRoot`'s component-count heuristic - ([File: executors/docker/internal/volumes/parser/windows_path.go])

### Summary
`windowsPath.IsRoot` in the non-Windows build (`!windows`) approximates UNC root detection by counting forward-slash-normalized path components and testing `components < 3`. This heuristic does not account for the Windows extended-length UNC prefix `\\?\UNC\`, so a semantically identical share-root path expressed with that prefix produces 4 components instead of 2, causing `IsRoot` to incorrectly return `false` for a genuine share root.

### Finding Description
`p.IsRoot` at [1](#0-0)  determines UNC-ness with `unc := strings.HasPrefix(path, "//") || strings.HasPrefix(path, "\\")` and then classifies the path as root only if the slash-count of the cleaned/converted path is `< 3`. For `\\server\share`, `convert` produces `/server/share` (2 separators) → correctly flagged root, matching the existing test case `"UNC share name"` [2](#0-1) .

However, Windows treats `\\?\UNC\server\share` as fully equivalent to `\\server\share` (the same share root), via the extended-length path prefix. The heuristic in `convert` at [3](#0-2)  has no special-casing for `\\?\UNC\`; it simply replaces backslashes with slashes and calls `gopath.Clean`, treating `?` and `UNC` as ordinary path components. That turns the true 2-component share root into a 4-component path (`/?/UNC/server/share`), pushing it past the `components < 3` boundary and causing `IsRoot` to return `false` for what is actually a share root.

This function is reachable from `volumes.manager.absolutePath`, which is the sole root-path gate before a job-supplied destination is used for a Docker bind mount: [4](#0-3) . `Destination` originates from the job's `.gitlab-ci.yml` `services`/volume definitions, which is attacker-controlled input for pipeline authors.

Crucially, this component-counting implementation is only compiled under `//go:build !windows` [5](#0-4) . When gitlab-runner itself runs on an actual Windows host, `newWindowsPath()` instead delegates to `helpers/path.NewWindowsPath()`, which uses real `filepath.Clean`/`filepath.Dir` semantics: [6](#0-5) . That Go stdlib-based implementation correctly understands the `\\?\UNC\` prefix as a volume name. The vulnerable approximation is exercised only when gitlab-runner is built for a non-Windows OS (e.g., a Linux-hosted runner driving a remote Windows Docker daemon for Windows containers) — a real and documented supported deployment topology for Windows container support, so the code path is reachable in production, not merely in tests.

### Impact Explanation
If exploited, a job could specify a service/volume destination string that Windows/Docker resolves to the root of an entire host-exposed SMB share (e.g., `\\?\UNC\fileserver\ci-share`), but which `IsRoot` erroneously classifies as non-root. `absolutePath` in `manager.go` would then accept it instead of returning `errDirectoryIsRootPath`, and the manager would proceed to add it as a host volume bind, mounting the entire host share root into the container with write access — matching the exact scoped impact (host share takeover via boundary-condition path classification).

### Likelihood Explanation
Preconditions: gitlab-runner must be compiled for a non-Windows OS while targeting a Windows Docker daemon (a supported but less common deployment). The attacker needs only normal pipeline-author ability to define a `services`/volume mount destination string, and needs a scenario where the runner's Docker configuration/allowed-images restrictions permit specifying custom host bind destinations. Given these preconditions, the exploit is deterministic and repeatable — the same crafted path string always causes the same misclassification.

### Recommendation
Normalize the extended-length prefixes (`\\?\`, `\\?\UNC\`) to their canonical UNC/drive form before component counting in `convert`/`IsRoot`, or better, replace the ad-hoc slash-counting heuristic with logic that mirrors Go's `filepath` volume-name semantics (strip `\\?\` and `\\?\UNC\` to reconstruct `\\server\share` before counting), ensuring the `!windows` approximation matches the real Windows `helpers/path.windowsPath.IsRoot` behavior for all prefix variants.

### Proof of Concept
Add a fuzz/table test to `executors/docker/internal/volumes/parser/windows_path_test.go`:

```go
func TestWindowsIsRoot_ExtendedUNCPrefix(t *testing.T) {
    p := newWindowsPath()
    // Semantically identical share-root paths must all be classified as root.
    variants := []string{
        `\\server\share`,
        `\\server\share\`,
        `\\?\UNC\server\share`,
        `\\?\UNC\server\share\`,
    }
    for _, v := range variants {
        assert.True(t, p.IsRoot(v), "expected %q to be classified as share root", v)
    }
}
```

Expected result with current code: the first two assertions pass, but the `\\?\UNC\server\share` variants fail (`IsRoot` returns `false`), demonstrating the boundary bypass. A property/fuzz test could further assert: for any `server`/`share` pair, `IsRoot(\\server\share) == IsRoot(\\?\UNC\server\share)`.

### Citations

**File:** executors/docker/internal/volumes/parser/windows_path.go (L1-1)
```go
//go:build !windows
```

**File:** executors/docker/internal/volumes/parser/windows_path.go (L48-63)
```go
func (p *windowsPath) IsRoot(path string) bool {
	if windowsNamedPipeRe.MatchString(path) {
		return false
	}

	if !p.IsAbs(path) {
		return false
	}

	unc := strings.HasPrefix(path, "//") || strings.HasPrefix(path, "\\")
	components := strings.Count(p.convert(path, false), "/")
	if unc {
		return components < 3
	}
	return components < 2
}
```

**File:** executors/docker/internal/volumes/parser/windows_path.go (L74-85)
```go
func (p *windowsPath) convert(pathname string, dir bool) string {
	if len(pathname) > 1 && pathname[1] == ':' {
		pathname = pathname[:1] + pathname[2:]
	}
	pathname = strings.NewReplacer("\\", "/", ":", "/").Replace(pathname)
	pathname = gopath.Clean("/" + pathname)

	if dir && !strings.HasSuffix(pathname, "/") {
		return pathname + "/"
	}
	return pathname
}
```

**File:** executors/docker/internal/volumes/parser/windows_path_test.go (L134-141)
```go
		"UNC share name": {
			arg:      `\\server\path`,
			expected: true,
		},
		"UNC share root path": {
			arg:      `\\server\path\`,
			expected: true,
		},
```

**File:** executors/docker/internal/volumes/manager.go (L126-136)
```go
func (m *manager) absolutePath(dir string) (string, error) {
	if m.parser.Path().IsRoot(dir) {
		return "", errDirectoryIsRootPath
	}

	if m.parser.Path().IsAbs(dir) {
		return dir, nil
	}

	return m.parser.Path().Join(m.config.BasePath, dir), nil
}
```

**File:** helpers/path/windows_path.go (L33-40)
```go
func (p *windowsPath) IsRoot(path string) bool {
	if windowsNamedPipe.MatchString(path) {
		return false
	}

	path = filepath.Clean(path)
	return filepath.IsAbs(path) && filepath.Dir(path) == path
}
```
