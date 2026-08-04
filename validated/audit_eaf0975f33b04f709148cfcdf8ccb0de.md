### Title
Malformed/out-of-tree `exclude` pattern short-circuits all subsequent exclusion checks, causing unintended file inclusion - (`commands/helpers/file_archiver.go`)

### Summary
`fileArchiver.isExcluded` iterates over `c.Exclude` patterns and calls `findRelativePathInProject` on each pattern to resolve it relative to the working directory. If any pattern in the list fails to resolve (e.g. because it points outside the working directory, such as `../../*`), the function logs a warning and immediately `return false, ""` from *inside* the `for` loop, aborting evaluation of all remaining exclude patterns for that file — not just the offending one.

### Finding Description [1](#0-0) 

```go
func (c *fileArchiver) isExcluded(path string) (bool, string) {
	path = filepath.ToSlash(path)
	for _, pattern := range c.Exclude {
		relPattern, err := c.findRelativePathInProject(pattern)
		if err != nil {
			logrus.Warningf("isExcluded: %v", err.Error())
			return false, ""
		}
		...
	}
	return false, ""
}
```

`c.Exclude` is populated directly from the `artifacts:exclude` field of `.gitlab-ci.yml` (passed through as CLI `--exclude` args to the `artifacts-uploader`/`cache-archiver` helper commands), so its contents are fully attacker/pipeline-author controlled. `isExcluded` is invoked once per candidate file inside `process()` [2](#0-1) , and iterates `c.Exclude` in order for every file. Because the `return false, ""` is placed inside the loop body (rather than a `continue`), the very first pattern that fails `findRelativePathInProject` (e.g. a pattern resolving outside `c.wd`, or an absolute path outside the project) terminates the whole exclusion check for that file, so every pattern after the bad one in `c.Exclude` is never evaluated — for any file, for the rest of the archiving run.

This is confirmed by the existing unit test `Test_isExcluded`, which demonstrates that an out-of-project pattern (`../*.*` or `/foo/file.txt`) causes `isExcluded` to log a warning and return `false, ""` (fail open) [3](#0-2) . However, that test only exercises a single-pattern `Exclude` list, so it does not catch the fact that a bad pattern also silently disables every subsequent legitimate exclude rule.

### Impact Explanation
A pipeline author (attacker-controlled `.gitlab-ci.yml`) who places one syntactically valid but out-of-project `exclude` entry ahead of legitimate exclude rules (e.g. `exclude: ['../../*', 'secrets/*', 'debug/*']`) causes the runner to silently skip evaluating `secrets/*` and `debug/*` for every archived file. Files that maintainers/security tooling expect to be excluded from build artifacts (based on the declared exclude rules) end up included in the artifact archive uploaded to GitLab, defeating the intended containment of the `exclude` allowlist mechanism. This is a fail-open logic bug rather than a sandbox escape, but it concretely violates the stated invariant that "exclude/include path resolution must fail closed, not fail open."

### Likelihood Explanation
Fully reachable with attacker-controlled `.gitlab-ci.yml`: the `exclude` list is a standard, user-facing CI/CD feature (`artifacts:exclude`, `cache:key`/`cache:paths` exclude equivalents), and out-of-project patterns can arise both intentionally and accidentally (e.g. relative patterns using `../`, or interpolated CI variables that resolve unpredictably). No special privileges beyond authoring pipeline config are required, and the bug triggers deterministically on the first bad pattern in the list — 100% repeatable.

### Recommendation
Change the error-handling branch in `isExcluded` from `return false, ""` to `continue`, so a single malformed/out-of-tree exclude pattern only skips that pattern instead of aborting the loop and disabling all subsequent exclude rules:
```go
if err != nil {
    logrus.Warningf("isExcluded: %v", err.Error())
    continue
}
```
Additionally, consider surfacing this as a job-level warning/error (rather than only a debug-level `logrus.Warningf`) so pipeline authors are alerted their exclude configuration is broken.

### Proof of Concept
Add a Go unit test to `commands/helpers/file_archiver_test.go` extending `Test_isExcluded`/`TestExcludedFilePaths` style tests:
```go
func Test_isExcluded_BadPatternSkipsSubsequentRules(t *testing.T) {
    wd, err := os.Getwd()
    require.NoError(t, err)

    f := fileArchiver{
        wd: wd,
        Exclude: []string{
            "../../*",     // out-of-project pattern, causes findRelativePathInProject error
            "secrets.txt", // legitimate rule that should still exclude secrets.txt
        },
    }

    excluded, rule := f.isExcluded("secrets.txt")
    // Current (buggy) behavior: excluded == false, rule == ""
    // Expected (fixed) behavior:
    assert.True(t, excluded, "secrets.txt should still be excluded despite an earlier bad exclude pattern")
    assert.Equal(t, "secrets.txt", rule)
}
```
Running this against current code fails (`excluded == false`), demonstrating the fail-open behavior; after applying the `continue` fix it passes.

### Citations

**File:** commands/helpers/file_archiver.go (L65-88)
```go
func (c *fileArchiver) process(match string) bool {
	var absolute, relative string
	var err error

	absolute, err = filepath.Abs(match)
	if err == nil {
		// Let's try to find a real relative path to an absolute from working directory
		relative, err = filepath.Rel(c.wd, absolute)
	}

	if err == nil {
		// Process path only if it lives in our build directory
		if !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			excluded, rule := c.isExcluded(relative)
			if excluded {
				c.exclude(rule)
				return false
			}

			err = c.add(relative)
		} else {
			err = errors.New("not supported: outside build directory")
		}
	}
```

**File:** commands/helpers/file_archiver.go (L103-121)
```go
func (c *fileArchiver) isExcluded(path string) (bool, string) {
	// Both path and pattern need to be normalized with filepath.ToSlash().
	// Matching will fail with Windows machines using "\\" path separators and patterns with "/" path separators
	path = filepath.ToSlash(path)
	for _, pattern := range c.Exclude {
		relPattern, err := c.findRelativePathInProject(pattern)
		if err != nil {
			logrus.Warningf("isExcluded: %v", err.Error())
			return false, ""
		}
		relPattern = filepath.ToSlash(relPattern)
		excluded, err := doublestar.Match(relPattern, path)
		if err == nil && excluded {
			return true, pattern
		}
	}

	return false, ""
}
```

**File:** commands/helpers/file_archiver_test.go (L405-416)
```go
		`no match - pattern not in project`: {
			pattern: "../*.*",
			path:    "file.txt",
			match:   false,
			log:     "isExcluded: artifact path is not a subpath of project directory: ../*.*",
		},
		`no match - absolute pattern not in project`: {
			pattern: "/foo/file.txt",
			path:    "file.txt",
			match:   false,
			log:     "isExcluded: artifact path is not a subpath of project directory: /foo/file.txt",
		},
```
