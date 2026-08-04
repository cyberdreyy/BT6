### Title
`windowsPath.IsRoot` fails to detect UNC-equivalent paths with mixed leading separators, allowing `errDirectoryIsRootPath` bypass - (File: `executors/docker/internal/volumes/parser/windows_path.go`)

### Summary
`windowsPath.IsRoot` decides whether a path is a UNC share (threshold `<3` components) purely from the *raw, unnormalized* prefix of the input string (`strings.HasPrefix(path, "//")` or `strings.HasPrefix(path, "\\")`), while the component count it compares against is computed from a *normalized* path (`convert`, which folds `\`→`/`). Because Windows treats `/` and `\` as interchangeable separators, a path like `/\server\share` is functionally identical to the canonical UNC share root `\\server\share`, but the raw-prefix `unc` check does not recognize it, so the function applies the wrong (`<2`) threshold and returns `false` for what is actually a filesystem/share root.

### Finding Description
`manager.absolutePath` guards against root-path bind-mount destinations with: [1](#0-0) 

`IsRoot` itself: [2](#0-1) 

and the normalization helper it relies on for the component count: [3](#0-2) 

Root cause: `unc` is computed from the raw path's literal prefix bytes, but the count it's compared against comes from a path that has already had all `\` characters folded into `/` by `convert`. For an input such as `/\server\share`:
- `IsAbs` returns `true` (leading `/`).
- `unc := HasPrefix(path,"//") || HasPrefix(path,"\\")` evaluates `false`, because the first character is `/` and the second is `\`, matching neither literal check — even though this string is exactly what Windows normalizes to `\\server\share` (a canonical 2-leading-separator UNC prefix) once `/`→`\` folding is applied.
- `convert` then folds the same string to `/server/share` (2 slashes), and because `unc` was computed as `false`, the code applies the drive-root threshold `components < 2` (`2 < 2` = false) instead of the UNC threshold `components < 3` (`2 < 3` = true).
- `IsRoot` therefore returns `false` for a path that is semantically the root of a UNC share.

This directly bypasses the invariant in `manager.absolutePath` — `errDirectoryIsRootPath` is never returned, `IsAbs` returns `true` so the path is used as-is, and it is passed straight through to `appendVolumeBind`/Docker as a bind-mount destination: [4](#0-3) 

The existing unit tests only exercise canonical, single-separator-style prefixes (`\\server\path`, `//./pipe/...`) and never a mixed leading-separator case, so this gap is untested: [5](#0-4) 

Regarding attacker reachability: the vulnerable code path is reached whenever `manager.Create`/`addCacheVolume`/`addHostVolume` process a `Destination` string that ultimately comes from `m.parser.ParseVolume(volume)`, where `volume` for cache volumes is derived from job/pipeline-controlled cache path configuration (`cache: paths:`), i.e. a string that a normal pipeline author supplies: [6](#0-5) 

I could not fully trace, within this session, the exact call site in `executors/docker/docker.go`/`executors/docker/volume.go` that feeds job-supplied cache paths into `Manager.Create`, so the precise degree of attacker control over the raw destination string (vs. runner-admin-configured host volumes) is not fully confirmed from this investigation and should be verified directly in code before treating this as fully exploitable end-to-end.

### Impact Explanation
If an attacker-controlled destination string bypasses `IsRoot`, the container mount destination effectively becomes the container filesystem root (or the root of a mounted share), causing the bind source to overwrite/expose the entire container filesystem at that mount point — consistent with the scoped impact of exposing another job's overlapping cache/host volumes on a shared host, if such a mount destination is later reused by/overlaps with a sibling job's build/cache directories.

### Likelihood Explanation
The bug is a straightforward, deterministic string-handling defect (no timing/race dependency) reproducible with a single crafted string, so it is trivially repeatable once an attacker-controlled input reaches `IsRoot`. However, exploitability depends on two unverified factors: (1) whether a normal pipeline author can actually supply an arbitrary absolute destination string that reaches `manager.absolutePath` (cache-path-derived destinations are the most plausible vector, but this wasn't fully confirmed end-to-end in this session), and (2) whether the Windows Docker daemon itself accepts a mixed-separator string like `/\server\share` as a literal UNC-root mount destination rather than rejecting/re-normalizing it before the mount succeeds. Given these open verification points, likelihood should be treated as "plausible but not fully confirmed" rather than proven.

### Recommendation
Compute `unc` from the same normalized representation used for the component count (or normalize the path once up front, e.g. replace `\`→`/` before both the prefix check and the `Count` calculation), so that any input which normalizes to a canonical `//server/share`-style prefix is classified consistently, regardless of the original mix of `/` and `\` characters at the start of the raw string.

### Proof of Concept
```go
func TestWindowsIsRoot_MixedSeparatorUNCBypass(t *testing.T) {
    p := newWindowsPath()

    // Semantically equivalent to the canonical UNC share root `\\server\share`,
    // but with a mixed leading separator ("/" then "\").
    mixed := "/\\server\\share"
    canonical := `\\server\share`

    assert.True(t, p.IsRoot(canonical), "canonical UNC root must be detected as root")
    assert.True(t, p.IsRoot(mixed),
        "mixed-separator UNC root must also be detected as root (currently fails: returns false)")
}
```
Expected current behavior: `p.IsRoot(canonical)` returns `true`, but `p.IsRoot(mixed)` returns `false`, proving the inconsistency. A follow-up integration test should feed `mixed` as a cache/host volume destination through `manager.Create`/`absolutePath` and assert that `errDirectoryIsRootPath` is returned, matching behavior for `canonical`.

### Citations

**File:** executors/docker/internal/volumes/manager.go (L80-106)
```go
func (m *manager) Create(ctx context.Context, volume string) error {
	if len(volume) < 1 {
		return nil
	}

	parsedVolume, err := m.parser.ParseVolume(volume)
	if err != nil {
		return fmt.Errorf("parse volume: %w", err)
	}

	switch parsedVolume.Len() {
	case 2:
		err = m.addHostVolume(parsedVolume)
		if err != nil {
			err = fmt.Errorf("adding host volume: %w", err)
		}
	case 1:
		err = m.addCacheVolume(ctx, parsedVolume)
		if err != nil {
			err = fmt.Errorf("adding cache volume: %w", err)
		}
	default:
		err = fmt.Errorf("unsupported volume definition %s", volume)
	}

	return err
}
```

**File:** executors/docker/internal/volumes/manager.go (L108-124)
```go
func (m *manager) addHostVolume(volume *parser.Volume) error {
	var err error

	volume.Destination, err = m.absolutePath(volume.Destination)
	if err != nil {
		return fmt.Errorf("defining absolute path: %w", err)
	}

	err = m.managedVolumes.Add(volume.Destination)
	if err != nil {
		return fmt.Errorf("updating managed volume list: %w", err)
	}

	m.appendVolumeBind(volume)

	return nil
}
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

**File:** executors/docker/internal/volumes/parser/windows_path_test.go (L134-146)
```go
		"UNC share name": {
			arg:      `\\server\path`,
			expected: true,
		},
		"UNC share root path": {
			arg:      `\\server\path\`,
			expected: true,
		},
		"UNC path": {
			arg:      `\\server\path\sub-path`,
			expected: false,
		},
	}
```
