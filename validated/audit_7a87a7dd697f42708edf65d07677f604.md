### Title
Job variables can override the runner-computed `CI_SERVER_TLS_CA_FILE`/`CERT`/`KEY` environment values used by `writeGitSSLConfig`, redirecting `git config sslCAInfo`/`sslCert`/`sslKey` - ([File: shells/abstract.go])

### Summary
`Build.GetCITLSVariables()` correctly builds an internal-only list of TLS variables that is used solely to *decide* whether to emit `git config` calls, but the actual runtime value referenced by those calls is a live shell variable (`$CI_SERVER_TLS_CA_FILE` etc.) exported earlier in the same script by `writeExports` via `Build.GetAllVariables()`. Because the TLS variables are injected via `getBaseVariablesBeforeJob()`, which is placed *before* job variables in `GetAllVariables()`'s precedence chain, a job-defined variable with an identical key is exported later in the generated shell script and overwrites the runner's value in the process environment before `writeGitSSLConfig`'s `git config` command executes.

### Finding Description
- `Build.GetCITLSVariables()` (common/build.go:1906-1939) builds `Internal:true` variables keyed by `tls.VariableCAFile`/`VariableCertFile`/`VariableKeyFile` (helpers/tls/consts.go:1-9) from `b.TLSData`. This function is called twice: once inside `getBaseVariablesBeforeJob()` (common/build.go:1966) and once directly inside `writeGitSSLConfig` (shells/abstract.go:627) purely to check `variables.Get(variable) == ""`.
- `Build.GetAllVariables()` (common/build.go:2055-2076) concatenates, in order: resolved feature flags, `getBaseVariablesBeforeJob()` (includes the TLS vars), `getNonFeatureFlagJobVariables()` (raw job variables), then `getBaseVariablesAfterJob()`. There is **no deduplication or `Internal`-flag enforcement** anywhere in this pipeline — `Internal`/`Public` flags only control masking/exposure decisions elsewhere (e.g. `PublicOrInternal()` in common/spec/variables.go:29-36), not override protection.
- `AbstractShell.writeExports()` (shells/abstract.go:593-596) iterates `GetAllVariables()` in that order and calls `w.Variable(variable)` for each entry. `BashWriter.Variable()` (shells/bash.go:229-241) simply emits `export KEY=VALUE` (or writes a temp file for `File:true`), with no check for duplicate keys. Because bash executes exports sequentially, **the last export for a given key wins** — and since job variables are appended after the base-before-job TLS variables, a job variable literally named `CI_SERVER_TLS_CA_FILE` (or `_CERT_FILE`/`_KEY_FILE`) is exported last and clobbers the runner-computed value in the shell process environment.
- `writeGitSSLConfig` (shells/abstract.go:620-642) then emits `git config http.<host>.sslCAInfo $CI_SERVER_TLS_CA_FILE` using `w.EnvVariableKey(variable)`, which for bash is literally `"$CI_SERVER_TLS_CA_FILE"` (shells/bash.go:219-221) — a live shell reference resolved at script-runtime, not a value baked in during script generation. Since this command runs later in the same script (e.g. `writeGetSourcesScript`, shells/abstract.go:504-516, which calls `writeExports` then `writeGitSSLConfig`), it reads the job-overridden value, not the runner's.
- The gating check `variables.Get(variable) == ""` in `writeGitSSLConfig` uses the correct, uncontaminated `GetCITLSVariables()` list, so the *decision to emit the config line* is unaffected — but the *value substituted at runtime* is not. This is the root cause: check-time value and use-time value are decoupled.
- This asymmetry is inconsistent with other internal variables: e.g. `CI_BUILD_NETWORK_NAME` is deliberately placed in `getBaseVariablesAfterJob()` (common/build.go:1972-1991), i.e. exported *after* job variables, and a dedicated regression test (`common/build_test.go:2413-2421`, "CI_BUILD_NETWORK_NAME cannot be overridden by job variables") confirms this protection pattern exists elsewhere in the codebase — but it was not applied to the TLS variables, which remain in `getBaseVariablesBeforeJob()`.

### Impact Explanation
An unprivileged pipeline author can define a job/pipeline-level CI/CD variable named `CI_SERVER_TLS_CA_FILE` (or `CI_SERVER_TLS_CERT_FILE`/`CI_SERVER_TLS_KEY_FILE`) pointing at a path they control (or a dotenv artifact value from an earlier stage). During `get_sources` (and other stages that call `writeGitSSLConfig`, e.g. `setupExternalGitConfig` in shells/abstract.go:825-839), the runner will run `git config http.<remote-host>.sslCAInfo "$CI_SERVER_TLS_CA_FILE"`, and because the environment variable was overwritten by the job's own export, git's per-host SSL config for the GitLab server host is set to point at attacker-chosen content/path instead of the runner-provisioned CA chain. This can undermine the CA-pinning trust model documented for the Runner (docs/configuration/tls-self-signed.md:107-113: "This approach is secure, but makes the Runner a single point of trust") for subsequent git operations against that host within the job (secondary clones/fetches, artifact-related git operations) that rely on this per-host git config.

### Likelihood Explanation
- Feasible with only standard job-variable definition capability (`.gitlab-ci.yml` `variables:` block, pipeline/job UI variables, or a dotenv artifact from a prior stage) — no elevated permissions required.
- Reachable through the normal shell-executor script generation path (`writeGetSourcesScript` → `writeExports` → `writeGitSSLConfig`), applicable to bash/pwsh/powershell writers.
- Deterministic and repeatable given the concatenation order in `GetAllVariables()`.

### Recommendation
Enforce non-overridability for `Internal:true` variables at the point they are merged with job variables, not just by placement order. Concretely:
1. In `Build.GetAllVariables()` (common/build.go), filter job variables (`getNonFeatureFlagJobVariables()`) to strip any entry whose `Key` collides with an `Internal:true` variable already added via `getBaseVariablesBeforeJob()`/`getBaseVariablesAfterJob()`, or move TLS variable injection to occur strictly after job variables (mirroring the `CI_BUILD_NETWORK_NAME` pattern) and additionally de-duplicate by key keeping the last-added *internal* one.
2. Alternatively/complementarily, have `writeGitSSLConfig` avoid depending on the live shell environment variable at all — bake the resolved file path directly into the emitted `git config` command (as a shell-escaped literal) rather than referencing `$CI_SERVER_TLS_CA_FILE`, since the value is already known at script-generation time from `build.GetCITLSVariables()`.
3. Add a generic guard/test (in the spirit of `TestDefaultVariables`'s `CI_BUILD_NETWORK_NAME cannot be overridden by job variables` case) asserting that no `Internal:true` variable's exported/consumed value can be shadowed by same-named job variables.

### Proof of Concept
```go
// shells/abstract_test.go
func TestWriteGitSSLConfig_JobVariableCannotShadowTLSVar(t *testing.T) {
    shell := AbstractShell{}
    build := &common.Build{
        Runner: &common.RunnerConfig{},
        Job: spec.Job{
            GitInfo: spec.GitInfo{
                RepoURL: "https://gitlab-ci-token:xxx@example.com:3443/project/repo.git",
            },
            TLSData: spec.TLSData{
                CAChain: "RUNNER_CA_CHAIN",
            },
            // Attacker-controlled job variable with the same key as the internal TLS var.
            Variables: spec.Variables{
                {Key: tls.VariableCAFile, Value: "/tmp/attacker-ca.pem"},
            },
        },
    }

    // 1. Simulate script generation: writeExports would export the internal
    //    value first, then the job value, last-write-wins in the shell env.
    allVars := build.GetAllVariables()
    // Find index of each CI_SERVER_TLS_CA_FILE occurrence; assert the
    // Internal:true one is NOT the last occurrence (demonstrating the bug).
    var lastIdx, internalIdx = -1, -1
    for i, v := range allVars {
        if v.Key == tls.VariableCAFile {
            lastIdx = i
            if v.Internal {
                internalIdx = i
            }
        }
    }
    require.NotEqual(t, -1, internalIdx, "internal TLS var must exist")
    // BUG: currently lastIdx != internalIdx when a job variable of the same
    // key is present, meaning the job-defined value is exported last and
    // wins in the shell environment.
    assert.Equal(t, internalIdx, lastIdx,
        "Internal CI_SERVER_TLS_CA_FILE must be the last-exported occurrence so it cannot be shadowed by job variables")

    // 2. Independently, confirm writeGitSSLConfig still references the
    //    variable by shell-env key ($CI_SERVER_TLS_CA_FILE), i.e. its emitted
    //    command is subject to whatever the shell env resolves at runtime,
    //    not the Go-side computed value.
    mockWriter := NewMockShellWriter(t)
    mockWriter.On("EnvVariableKey", tls.VariableCAFile).Return("$CI_SERVER_TLS_CA_FILE")
    mockWriter.On("CommandArgExpand", "git", "config",
        "http.https://example.com:3443.sslCAInfo", "$CI_SERVER_TLS_CA_FILE").Once()
    shell.writeGitSSLConfig(mockWriter, build, nil)
}
```
Expected assertion failure on current code: the internal `CI_SERVER_TLS_CA_FILE` occurrence is not the last one in `GetAllVariables()` when a job variable of the same key is supplied, proving the job-controlled value is what ends up in the shell environment consumed by the `git config sslCAInfo` command. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** common/build.go (L1906-1939)
```go
func (b *Build) GetCITLSVariables() spec.Variables {
	variables := spec.Variables{}

	if b.TLSData.CAChain != "" {
		variables = append(variables, spec.Variable{
			Key:      tls.VariableCAFile,
			Value:    b.TLSData.CAChain,
			Public:   true,
			Internal: true,
			File:     true,
		})
	}

	if b.TLSData.AuthCert != "" && b.TLSData.AuthKey != "" {
		variables = append(
			variables,
			spec.Variable{
				Key:      tls.VariableCertFile,
				Value:    b.TLSData.AuthCert,
				Public:   true,
				Internal: true,
				File:     true,
			},
			spec.Variable{
				Key:      tls.VariableKeyFile,
				Value:    b.TLSData.AuthKey,
				Internal: true,
				File:     true,
			},
		)
	}

	return variables
}
```

**File:** common/build.go (L1952-1969)
```go
// getBaseVariablesBeforeJob returns the base variables that come before job variables.
func (b *Build) getBaseVariablesBeforeJob() spec.Variables {
	variables := make(spec.Variables, 0)

	if b.Image.Name != "" {
		variables = append(
			variables,
			spec.Variable{Key: "CI_JOB_IMAGE", Value: b.Image.Name, Public: true, Internal: true, File: false},
		)
	}
	if b.Runner != nil {
		variables = append(variables, b.Runner.GetVariables()...)
	}
	variables = append(variables, b.GetDefaultVariables()...)
	variables = append(variables, b.GetCITLSVariables()...)

	return variables
}
```

**File:** common/build.go (L2050-2076)
```go
// GetAllVariables() returns final variables with a consistent precedence order:
// 1. Resolved feature flags (TOML takes precedence over job variables)
// 2. Base variables that come before job variables
// 3. Job variables (excluding feature flags to prevent overriding resolved values)
// 4. Base variables that come after job variables
func (b *Build) GetAllVariables() spec.Variables {
	if b.allVariables != nil {
		return b.allVariables
	}

	// Phase 1: Ensure feature flags have been resolved.
	if b.buildSettings == nil {
		b.Settings()
	}

	variables := make(spec.Variables, 0)

	// Phase 2: Add resolved feature flags first (maintains original precedence order)
	variables = append(variables, b.getResolvedFeatureFlags()...)
	variables = append(variables, b.getBaseVariablesBeforeJob()...)
	variables = append(variables, b.getNonFeatureFlagJobVariables()...)
	variables = append(variables, b.getBaseVariablesAfterJob()...)

	b.allVariables = variables.Expand()

	return b.allVariables
}
```

**File:** helpers/tls/consts.go (L1-7)
```go
package tls

const (
	VariableCAFile   string = "CI_SERVER_TLS_CA_FILE"
	VariableCertFile string = "CI_SERVER_TLS_CERT_FILE"
	VariableKeyFile  string = "CI_SERVER_TLS_KEY_FILE"
)
```

**File:** shells/abstract.go (L504-516)
```go
func (b *AbstractShell) writeGetSourcesScript(_ context.Context, w ShellWriter, info common.ShellScriptInfo) error {
	b.writeExports(w, info)

	w.Variable(spec.Variable{Key: "GIT_TERMINAL_PROMPT", Value: "0"})
	w.Variable(spec.Variable{Key: "GCM_INTERACTIVE", Value: "Never"})

	if err := b.setupTokenlessGitConfig(w, info.Build); err != nil {
		return err
	}

	if !info.Build.IsSharedEnv() {
		b.writeGitSSLConfig(w, info.Build, []string{"--global"})
	}
```

**File:** shells/abstract.go (L593-642)
```go
func (b *AbstractShell) writeExports(w ShellWriter, info common.ShellScriptInfo) {
	for _, variable := range info.Build.GetAllVariables() {
		w.Variable(variable)
	}

	gitlabEnvFile := w.TmpFile(gitlabEnvFileName)

	w.Variable(spec.Variable{
		Key:   "GITLAB_ENV",
		Value: gitlabEnvFile,
	})

	w.SourceEnv(gitlabEnvFile)

	// Re-exported every stage (git reads the env var live) so the seed file
	// applies to every git invocation regardless of CWD. ExportRaw + TmpFile
	// resolves the path at script-runtime without the variable-quoting path
	// mangling it; emitted after SourceEnv so the job can't shadow it.
	if info.Build.IsFeatureFlagOn(featureflags.GitURLsWithoutTokens) {
		w.ExportRaw("GIT_CONFIG_GLOBAL", w.TmpFile(globalGitConfigSeedFile))
	}
}

func (b *AbstractShell) writeCacheExports(w ShellWriter, variables map[string]string) string {
	return w.DotEnvVariables(gitlabCacheEnvFileName, variables)
}

func (b *AbstractShell) writeGitSSLConfig(w ShellWriter, build *common.Build, where []string) {
	host, err := b.getRemoteHost(build)
	if err != nil {
		w.Warningf("git SSL config: Can't get repository host. %v", err)
		return
	}

	variables := build.GetCITLSVariables()
	args := append([]string{"config"}, where...)

	for variable, config := range map[string]string{
		tls.VariableCAFile:   "sslCAInfo",
		tls.VariableCertFile: "sslCert",
		tls.VariableKeyFile:  "sslKey",
	} {
		if variables.Get(variable) == "" {
			continue
		}

		key := fmt.Sprintf("http.%s.%s", host, config)
		w.CommandArgExpand("git", append(args, key, w.EnvVariableKey(variable))...)
	}
}
```

**File:** shells/bash.go (L219-241)
```go
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
}
```

**File:** common/build_test.go (L2413-2421)
```go
		"CI_BUILD_NETWORK_NAME cannot be overridden by job variables": {
			jobVariables: spec.Variables{
				{Key: featureflags.NetworkPerBuild, Value: "true"},
				{Key: "CI_BUILD_NETWORK_NAME", Value: "user-override"},
			},
			rootDir:       "/builds",
			key:           "CI_BUILD_NETWORK_NAME",
			expectedValue: "runner-1234-0-0-0",
		},
```

**File:** common/spec/variables.go (L29-36)
```go
func (b Variables) PublicOrInternal() (variables Variables) {
	for _, variable := range b {
		if variable.Public || variable.Internal {
			variables = append(variables, variable)
		}
	}
	return variables
}
```
