### Title
Path traversal in secret `variableKey` allows file-type secrets to be written outside `TemporaryPath` - ([File: common/secrets.go], [File: shells/bash.go], [File: shells/powershell.go])

### Summary
`defaultSecretsResolver.handleSecret` takes the `secrets:` map key (`variableKey`) directly from the attacker-controlled `.gitlab-ci.yml` and places it verbatim into `spec.Variable.Key` with no validation of its character set. When the variable is written to disk as a file-type secret, `BashWriter.Variable`/`PsWriter.Variable` build the destination path with `path.Join(TemporaryPath, variable.Key)`, which normalizes `..` segments and can walk the resulting path outside `TemporaryPath`.

### Finding Description
`defaultSecretsResolver.Resolve` iterates over the pipeline-author-controlled `spec.Secrets` map and calls `handleSecret(variableKey, secret)` for each key, building `spec.Variable{Key: variableKey, File: secret.IsFile()}` with no sanitization of `variableKey`: [1](#0-0) 

By default `secret.IsFile()` returns `true` unless the pipeline author explicitly sets `File: false`: [2](#0-1) 

Downstream, `BashWriter.Variable` computes the on-disk path for a file-type variable via `TmpFile`, which does `path.Join(b.TemporaryPath, name)` followed by `cleanPath`/`Absolute`: [3](#0-2) 

Go's `path.Join` calls `Clean` on the joined result, which collapses `..` components against the preceding path elements. When `TemporaryPath` is an absolute path (the normal case for `build.TmpProjectDir()`), a `variableKey` containing enough `../` segments will pop past all of `TemporaryPath`'s own components and land on an attacker-chosen absolute path (e.g. `variableKey = "../../../../etc/passwd"` against a 3-level `TemporaryPath` resolves to `/etc/passwd`). The `PsWriter.Variable` implementation has the identical pattern for PowerShell/Windows: [4](#0-3) 

The resulting `variableFile` is quoted with `%q`/`resolvePath` only for shell-string-escaping purposes (to keep it a syntactically valid quoted string) — quoting does not re-validate or restrict where the path points. The `printf '%s' ... > <path>` (bash) or `[System.IO.File]::WriteAllText(<path>, ...)` (PowerShell) command then performs a literal file write to that resolved, potentially-escaped path when the generated script runs inside the job.

No check in this call chain enforces that `variable.Key` for file-type variables is a bare identifier (e.g. no `/`, no `..`). The unit tests (`Test_BashWriter_Variable` in `shells/bash_test.go`) only cover well-formed keys like `"KEY"`, not path-traversal payloads, so this gap is not caught by existing tests: [5](#0-4) 

### Impact Explanation
An unprivileged pipeline author who can write a `secrets:` block (any CI job author) can choose the map key used to name a resolved secret. Because that raw string becomes `spec.Variable.Key` and file-type secrets are written by joining it onto `TemporaryPath` without segment validation, the author can make the generated build script write the plaintext secret value to a file path outside the job's build/cache/artifact root — inside whatever filesystem namespace the job's shell/PowerShell process can reach (container filesystem for Docker/Kubernetes executors, or the runner host filesystem for the shell executor). This is a concrete violation of the "file operations must stay within intended build/cache/artifact roots" invariant, independent of any admin misconfiguration.

### Likelihood Explanation
Preconditions are minimal and fully within an ordinary pipeline author's control: they only need permission to add a `secrets:` section to a job (a standard CI/CD feature, not a privileged operation). The exploit is deterministic — `path.Join` semantics reliably collapse `../` sequences the same way every time, so the resulting path is fully attacker-computable given knowledge (or brute-forcing) of `TemporaryPath`'s directory depth (which is predictable/discoverable, e.g. via other job output or standard runner path conventions). This makes the finding highly repeatable via a simple crafted `.gitlab-ci.yml`.

### Recommendation
Validate/sanitize `variableKey` before it is placed into `spec.Variable.Key` in `handleSecret` (or at the point `spec.Variable` is consumed for file writes in `BashWriter.Variable` / `PsWriter.Variable`). Reject or strip path separators and `..` segments, and/or use `filepath.Base` combined with an explicit post-join containment check (verify the resolved absolute path has `TemporaryPath` as a prefix after `filepath.Clean`) before performing the write, failing the job if the check fails.

### Proof of Concept
```go
func Test_BashWriter_Variable_PathTraversal(t *testing.T) {
    w := BashWriter{TemporaryPath: "/builds/project/0"}
    w.Variable(spec.Variable{
        Key:   "../../../../etc/passwd",
        Value: "attacker-controlled-secret",
        File:  true,
    })
    out := w.String()

    // Assert the write target stays under TemporaryPath.
    assert.NotContains(t, out, `"/etc/passwd"`,
        "file-type variable escaped TemporaryPath sandbox: %s", out)
    assert.Contains(t, out, w.TemporaryPath,
        "expected write path to remain rooted at TemporaryPath")
}
```
Expected current (buggy) behavior: the generated script contains `printf '%s' $'attacker-controlled-secret' > "/etc/passwd"`, failing the `NotContains` assertion and demonstrating the sandbox escape. An equivalent test can be written against `PsWriter.Variable` using `resolvePath`/`TmpFile` on Windows-style paths.

### Citations

**File:** common/secrets.go (L99-146)
```go
	variables := make(spec.Variables, 0)
	for variableKey, secret := range secrets {
		r.logger.Println(fmt.Sprintf("Resolving secret %q...", variableKey))

		v, err := r.handleSecret(variableKey, secret)
		if err != nil {
			return nil, err
		}

		if v != nil {
			variables = append(variables, *v)
		}
	}

	return variables, nil
}

func (r *defaultSecretsResolver) handleSecret(variableKey string, secret spec.Secret) (*spec.Variable, error) {
	sr, err := r.secretResolverRegistry.GetFor(secret)
	if err != nil {
		r.logger.Warningln(fmt.Sprintf("Not resolved: %v", err))
		return nil, nil
	}

	r.logger.Println(fmt.Sprintf("Using %q secret resolver...", sr.Name()))

	value, err := sr.Resolve()
	if errors.Is(err, ErrSecretNotFound) {
		if !r.featureFlagOn(featureflags.EnableSecretResolvingFailsIfMissing) {
			err = nil
		} else {
			err = fmt.Errorf("%w: %v", err, variableKey)
		}
	}
	if err != nil {
		return nil, err
	}

	variable := &spec.Variable{
		Key:    variableKey,
		Value:  value,
		File:   secret.IsFile(),
		Masked: true,
		Raw:    true,
	}

	return variable, nil
}
```

**File:** common/spec/spec.go (L674-684)
```go
// IsFile defines whether the variable should be of type FILE or no.
//
// The default behavior is to represent the variable as FILE type.
// If defined by the user - set to whatever was chosen.
func (s Secret) IsFile() bool {
	if s.File == nil {
		return true
	}

	return *s.File
}
```

**File:** shells/bash.go (L211-240)
```go
func (b *BashWriter) TmpFile(name string) string {
	return b.cleanPath(path.Join(b.TemporaryPath, name))
}

func (b *BashWriter) cleanPath(name string) string {
	return b.Absolute(name)
}

func (b *BashWriter) EnvVariableKey(name string) string {
	return fmt.Sprintf("$%s", name)
}

// Intended to be used on unmodified paths only (i.e. paths that have not been
// cleaned with cleanPath()).
func (b *BashWriter) isTmpFile(path string) bool {
	return strings.HasPrefix(path, b.TemporaryPath)
}

func (b *BashWriter) Variable(variable spec.Variable) {
	if variable.File {
		variableFile := b.TmpFile(variable.Key)
		b.Linef("mkdir -p %q", helpers.ToSlash(b.TemporaryPath))
		b.Linef("printf '%%s' %s > %q", b.escape(variable.Value), variableFile)
		b.Linef("export %s=%q", b.escape(variable.Key), variableFile)
	} else {
		if b.isTmpFile(variable.Value) {
			variable.Value = b.cleanPath(variable.Value)
		}
		b.Linef("export %s=%s", b.escape(variable.Key), b.escape(variable.Value))
	}
```

**File:** shells/powershell.go (L434-457)
```go
func (p *PsWriter) isTmpFile(path string) bool {
	return strings.HasPrefix(path, p.TemporaryPath)
}

func (p *PsWriter) Variable(variable spec.Variable) {
	if variable.File {
		variableFile := p.TmpFile(variable.Key)
		p.MkDir(p.TemporaryPath)
		p.Linef(
			"[System.IO.File]::WriteAllText(%s, %s)",
			p.resolvePath(variableFile),
			psQuoteVariable(variable.Value),
		)
		p.Linef("${%s}=%s", variable.Key, p.resolvePath(variableFile))
	} else {
		if p.isTmpFile(variable.Value) {
			variable.Value = p.cleanPath(variable.Value)
		}

		p.Linef("${%s}=%s", variable.Key, psQuoteVariable(variable.Value))
	}

	p.Linef("${env:%s}=${%s}", variable.Key, variable.Key)
}
```

**File:** shells/bash_test.go (L187-228)
```go
func Test_BashWriter_Variable(t *testing.T) {
	tests := map[string]struct {
		variable spec.Variable
		writer   BashWriter
		want     string
	}{
		"file var, relative path": {
			variable: spec.Variable{Key: "KEY", Value: "the secret", File: true},
			writer:   BashWriter{TemporaryPath: "foo/bar"},
			// nolint:lll
			want: "mkdir -p \"foo/bar\"\nprintf '%s' $'the secret' > \"$PWD/foo/bar/KEY\"\nexport KEY=\"$PWD/foo/bar/KEY\"\n",
		},
		"file var, absolute path": {
			variable: spec.Variable{Key: "KEY", Value: "the secret", File: true},
			writer:   BashWriter{TemporaryPath: "/foo/bar"},
			// nolint:lll
			want: "mkdir -p \"/foo/bar\"\nprintf '%s' $'the secret' > \"/foo/bar/KEY\"\nexport KEY=\"/foo/bar/KEY\"\n",
		},
		"tmp file var, relative path": {
			variable: spec.Variable{Key: "KEY", Value: "foo/bar/KEY2"},
			writer:   BashWriter{TemporaryPath: "foo/bar"},
			want:     "export KEY=$'$PWD/foo/bar/KEY2'\n",
		},
		"tmp file var, absolute path": {
			variable: spec.Variable{Key: "KEY", Value: "/foo/bar/KEY2"},
			writer:   BashWriter{TemporaryPath: "/foo/bar"},
			want:     "export KEY=/foo/bar/KEY2\n",
		},
		"regular var": {
			variable: spec.Variable{Key: "KEY", Value: "VALUE"},
			writer:   BashWriter{TemporaryPath: "/foo/bar"},
			want:     "export KEY=VALUE\n",
		},
	}

	for name, tt := range tests {
		t.Run(name, func(t *testing.T) {
			tt.writer.Variable(tt.variable)
			assert.Equal(t, tt.want, tt.writer.String())
		})
	}
}
```
