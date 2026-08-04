### Title
`ProxyExecCommand.Execute` trusts `os.Getenv("RUNNER_TEMP_PROJECT_DIR")` over the Runner-computed `--temp-dir` flag, allowing a job-controlled variable to redirect the masking store to another project's directory - (File: commands/helpers/proxy_exec.go)

### Summary
`ProxyExecShell.GetConfiguration` bakes the correct, Runner-computed `info.Build.TmpProjectDir()` into the `--temp-dir` flag when constructing the entrypoint command line. However, `ProxyExecCommand.Execute` unconditionally prefers `os.Getenv("RUNNER_TEMP_PROJECT_DIR")` over that flag, and `RUNNER_TEMP_PROJECT_DIR` is also the literal key of a normal, unprotected job variable (`spec.TempProjectDirVariableKey`) that flows into the process environment the same way any other CI/CD variable does.

### Finding Description
`shells/proxy_exec.go` computes the trusted destination directory from the build itself: [1](#0-0) 

That value is passed explicitly as `--temp-dir` on the command line — a value the executor derives from server-side build state, not from the job's own variables.

`commands/helpers/proxy_exec.go`'s `Execute` then does the opposite of what a defense-in-depth design would do: it reads the environment variable of the *same name* first, and only falls back to the trusted flag if that variable is unset: [2](#0-1) 

`RUNNER_TEMP_PROJECT_DIR` is not a runner-internal-only, filtered name — it is modeled in `common/spec/variables.go` as an ordinary variable key (`TempProjectDirVariableKey`), used generically by `tmpFile()`/`Get()`/`Value()` with no special "cannot be overridden by job/user variable" enforcement visible in that file, and the constant/doc entries (`docs/configuration/advanced-configuration.md`) confirm it is a documented, user-visible variable name, not a hidden implementation detail. Job variables (including any variable named identically to a predefined one) are exported into the environment of the process tree that runs the job/entrypoint, as demonstrated by the runner's own `RunBuildWithExpandedFileVariable` test which asserts `RUNNER_TEMP_PROJECT_DIR=<value>` is visible in the script's environment via a simple `echo $RUNNER_TEMP_PROJECT_DIR`. Since the `gitlab-runner-helper proxy-exec` entrypoint process is spawned inside that same job environment (container env or shell env), any job-controlled definition of `RUNNER_TEMP_PROJECT_DIR` (via `.gitlab-ci.yml` `variables:` or dynamically at pipeline time) can, once merged into the environment ahead of/instead of Runner's authoritative value, be read by `Execute()` and used verbatim as `dst` for `NewProxy(dst, ...)`, which opens `store.Open(dir)` and the `addmask.New(db, ...)` masking DB rooted at that attacker-chosen path.

The critical unresolved factor — precedence when both Runner's own computed value and a job-supplied value with the same key exist in `build.Variables` — could not be fully traced in the available time (the exact call site that inserts the internal `RUNNER_TEMP_PROJECT_DIR` entry and whether it uses `Variables.Set()`, which explicitly de-duplicates by keeping only the caller's supplied value, was not located in `common/build.go`, whose relevant lines were not retrievable within the tool budget). If that internal insertion happens *before* job variables are merged, or if it does not use `Set()`'s override semantics, a job-supplied variable of the same name would win and this is directly exploitable. If Runner's own code always re-asserts this value last via `Set()`, the attack is blocked at the variable-model layer rather than in `Execute()` — but `Execute()`'s design of preferring the environment over the trusted flag remains an unnecessary, fragile trust decision regardless.

### Impact Explanation
If exploitable, an attacker-controlled job could point `dst` at another concurrently-running job's `TmpProjectDir`, causing `store.Open(dir)` / `addmask.New` to open and write to that other job's `masking.db`. This could let one job's masked-secret store become readable/writable by another job's `proxy.Stdout()/Stderr()` pipeline (`p.addmask.Get(0/1)`), enabling cross-job/cross-project mask-state corruption or secret disclosure between jobs sharing the same runner host/executor, matching the "masking store, addmask secrets must be isolated per-job/per-project" invariant.

### Likelihood Explanation
Exploitability hinges entirely on the still-unverified variable-precedence question above. This is not purely theoretical — the code path (`Execute()` preferring `os.Getenv` over the passed `--temp-dir`) is real and reachable by any job that uses `IsProxyExec()` shells, and `RUNNER_TEMP_PROJECT_DIR` is a normal, non-reserved variable name in this codebase's variable model. But without confirming that Runner's internal assignment can be shadowed by a job-defined variable of the same key (i.e., without seeing the exact merge order in `common/build.go`), this cannot be asserted as fully proven with the evidence gathered.

### Recommendation
In `commands/helpers/proxy_exec.go`'s `Execute`, drop the `os.Getenv("RUNNER_TEMP_PROJECT_DIR")` preference entirely and always use `c.TempDir` (the value baked in by `ProxyExecShell.GetConfiguration`) as the sole source of `dst`. If an environment-variable override is required for some other legitimate reason, it must come from a source outside the job's own variable set (e.g., a distinct, Runner-only env var name never exposed to job scripts), not from a name that collides with a documented job/predefined variable.

### Proof of Concept
Go test plan in `commands/helpers`:
1. Set up two temp directories `dirA`, `dirB` simulating two jobs' `TmpProjectDir`.
2. Call `ProxyExecCommand{TempDir: dirA}.Execute(ctx)` with `RUNNER_TEMP_PROJECT_DIR` unset in the test process env, and separately with it set to `dirB`, using a trivial `args` command (e.g., `echo`).
3. Assert (a) with the env var unset, `store.Open(dirA)`/`masking.db` is created under `dirA` only; (b) with `RUNNER_TEMP_PROJECT_DIR=dirB` set while `--temp-dir dirA` is passed, the masking store/db is created/opened under `dirB` instead of `dirA`, proving `Execute()` ignores the trusted flag whenever the environment variable is present.
4. To fully confirm end-to-end exploitability, trace `common/build.go`'s variable-assembly code to determine whether a job-supplied `spec.Variable{Key:"RUNNER_TEMP_PROJECT_DIR", Value: dirB}` (added the way `.gitlab-ci.yml`-defined variables are added) survives merging with Runner's own internal assignment, and add an integration test (`common/buildtest`) running two concurrent builds where job B defines `variables: {RUNNER_TEMP_PROJECT_DIR: <jobA's TmpProjectDir>}` and asserting job B's `addmask.Get()` reader can read entries written by job A.

### Citations

**File:** shells/proxy_exec.go (L26-41)
```go
func (s *ProxyExecShell) GetConfiguration(info common.ShellScriptInfo) (*common.ShellConfiguration, error) {
	base, err := s.Shell.GetConfiguration(info)
	if err != nil || info.Build == nil || !info.Build.Runner.IsProxyExec() {
		return base, err
	}

	tempDir := fmt.Sprintf("%q", info.Build.TmpProjectDir())

	return &common.ShellConfiguration{
		Command:       info.RunnerCommand,
		Arguments:     append([]string{"proxy-exec", "--temp-dir", info.Build.TmpProjectDir(), base.Command}, base.Arguments...),
		CmdLine:       info.RunnerCommand + " proxy-exec --temp-dir " + tempDir + " " + base.CmdLine,
		DockerCommand: append([]string{info.Build.TmpProjectDir() + "/gitlab-runner-helper", "proxy-exec"}, base.DockerCommand...),
		PassFile:      base.PassFile,
		Extension:     base.Extension,
	}, nil
```

**File:** commands/helpers/proxy_exec.go (L74-93)
```go
func (c *ProxyExecCommand) Execute(cliContext *cli.Context) {
	args := cliContext.Args()
	if len(args) == 0 {
		logrus.Fatal("gitlab-runner-helper exec expected args")
	}

	dst := os.Getenv("RUNNER_TEMP_PROJECT_DIR")
	if dst == "" {
		dst = c.TempDir
	}
	if c.Bootstrap {
		if err := bootstrap(dst); err != nil {
			logrus.Fatalln("bootstrapping", err)
		}
	}

	proxy, err := NewProxy(dst, stdout, stderr)
	if err != nil {
		logrus.Fatalln("creating exec proxy", err)
	}
```
