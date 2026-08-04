### Title
Attacker-influenced `job_env` entries can override reserved runtime control variables due to env-slice append ordering - ([File: executors/custom/custom.go](executors/custom/custom.go), function `prepareCommand`, and [executors/custom/command/command.go](executors/custom/command/command.go), function `New`)

### Summary
`prepareCommand` builds the process environment by appending `jobEnv` entries first, then `command.New` prepends `os.Environ()` plus the reserved constants (`BUILD_FAILURE_EXIT_CODE`, `SYSTEM_FAILURE_EXIT_CODE`, `BUILD_EXIT_CODE_FILE`, `JOB_RESPONSE_FILE`) *before* the caller-supplied `cmdOpts.Env` slice. Because `exec.Cmd.Env` resolves duplicate keys with last-value-wins semantics, the `jobEnv` values end up positioned after the reserved constants in the final slice and therefore win on collision, contrary to the intent signaled by the in-code comment.

### Finding Description
`prepareCommand` in [1](#0-0)  appends `jobEnv` entries into `cmdOpts.Env` first ("to avoid overwriting any CI/CD or predefined variables"), followed by `CUSTOM_ENV_*` variables. This `cmdOpts.Env` slice is then passed into `command.New`, where [2](#0-1)  builds `env := os.Environ()`, appends the reserved `defaultVariables` map (including `JOB_RESPONSE_FILE` and `BUILD_EXIT_CODE_FILE`) to it, and then does `cmdOpts.Env = append(env, cmdOpts.Env...)`. This places the previously-built `jobEnv` entries *after* the reserved constants in the final env slice.

Go's `exec.Cmd.Env` documents duplicate-key resolution as last-value-wins. Since `jobEnv` entries occupy a later position than the reserved constants in the final slice, any `jobEnv` key that collides with `BUILD_FAILURE_EXIT_CODE`, `SYSTEM_FAILURE_EXIT_CODE`, `BUILD_EXIT_CODE_FILE`, or `JOB_RESPONSE_FILE` (defined in [3](#0-2) ) silently overrides the runner-controlled value that the driver script would otherwise observe via the OS environment.

`jobEnv` originates from `ConfigExecOutput.JobEnv` ( [4](#0-3) ) and is injected via `InjectInto` ( [5](#0-4) ). This value is produced by the custom-executor driver's `config_exec` script output, which is under the runner-operator's control, not the pipeline author's directly. For this to be attacker-reachable, the driver script must itself copy an unprivileged pipeline author's job-variable name/value pair verbatim into its `job_env` output — this is the stated precondition and is plausible for drivers that implement generic "variable pass-through" logic, since CI/CD variable *names* can be freely chosen by pipeline authors (e.g., via manual pipeline/trigger variables), including names that collide with the reserved constants.

### Impact Explanation
If a driver reflects job variables into `job_env`, an unprivileged pipeline author can define a variable named `JOB_RESPONSE_FILE` or `BUILD_EXIT_CODE_FILE` and have its value override the reserved OS environment variable seen by the driver process, since `jobEnv` wins over the reserved constants in the final `exec.Cmd.Env` slice. Note, however, that GitLab Runner's own internal logic for reading the exit-code file (`command.parseBuildFailure`, [6](#0-5)  using `c.buildCodeFile`) is populated directly from the `Options` struct field, not re-read from the OS environment, so the Runner-side failure-classification read path itself is not misled. The concrete impact is therefore limited to confusing the driver script (which is expected to honor these documented env vars) about the true file paths — a functional/behavioral defect that could degrade to script-failure misclassification, rather than a direct Runner-enforced security-boundary bypass.

### Likelihood Explanation
Exploitability strictly depends on an already-required, non-default driver behavior: the custom-executor driver script must copy an attacker-controllable job-variable key/value verbatim into its `config_exec` JSON `job_env` output. This is a driver-implementation choice made by the runner operator, not something GitLab Runner itself does by default. Given that precondition, an unprivileged pipeline author can trivially choose a colliding variable name, making the ordering defect itself deterministic and repeatable.

### Recommendation
Reorder the environment construction so the reserved runtime constants are always applied last (i.e., after `jobEnv` and `CUSTOM_ENV_*`) in `command.New`, guaranteeting they cannot be overridden regardless of caller-supplied `cmdOpts.Env` content. Additionally, `prepareCommand` should explicitly filter/reject `jobEnv` keys that match the reserved names (`BUILD_FAILURE_EXIT_CODE`, `SYSTEM_FAILURE_EXIT_CODE`, `BUILD_EXIT_CODE_FILE`, `JOB_RESPONSE_FILE`) before appending them to `cmdOpts.Env`, and update the misleading comment about "appending job_env first" to reflect actual precedence semantics.

### Proof of Concept
Unit test in `executors/custom/custom_test.go` style:
1. Set `executor.jobEnv = map[string]string{"JOB_RESPONSE_FILE": "/tmp/attacker"}`.
2. Call `e.prepareCommand(ctx, opts)` to obtain `cmdOpts.Env`, then feed it into a stubbed `command.New` (or call the real `command.New` with `Options{JobResponseFile: "/real/path"}`).
3. Assert on the final `cmdOpts.Env` slice (or by constructing an `exec.Cmd` with that `Env` and calling `cmd.Environ()`/spawning a helper process that prints `os.Getenv("JOB_RESPONSE_FILE")`) that the resolved value is `/tmp/attacker`, not `/real/path` — demonstrating that the reserved constant is overridden by the attacker/driver-supplied `job_env` entry.

### Citations

**File:** executors/custom/custom.go (L71-73)
```go
	if c.JobEnv != nil {
		executor.jobEnv = *c.JobEnv
	}
```

**File:** executors/custom/custom.go (L259-262)
```go
	// Append job_env defined variable first to avoid overwriting any CI/CD or predefined variables.
	for k, v := range e.jobEnv {
		cmdOpts.Env = append(cmdOpts.Env, fmt.Sprintf("%s=%s", k, v))
	}
```

**File:** executors/custom/command/command.go (L56-68)
```go
	defaultVariables := map[string]string{
		"TMPDIR":                          cmdOpts.Dir,
		api.BuildFailureExitCodeVariable:  strconv.Itoa(BuildFailureExitCode),
		api.SystemFailureExitCodeVariable: strconv.Itoa(SystemFailureExitCode),
		api.BuildCodeFileVariable:         options.BuildExitCodeFile,
		api.JobResponseFileVariable:       options.JobResponseFile,
	}

	env := os.Environ()
	for key, value := range defaultVariables {
		env = append(env, fmt.Sprintf("%s=%s", key, value))
	}
	cmdOpts.Env = append(env, cmdOpts.Env...)
```

**File:** executors/custom/command/command.go (L120-145)
```go
func (c *command) parseBuildFailure(eerr *exec.ExitError) error {
	file, err := os.Open(c.buildCodeFile)
	if err != nil {
		// If the driver has not generated a file at the prescribed location
		// we revert to the default BuildError and exitCode.
		return &common.BuildError{Inner: eerr, ExitCode: BuildFailureExitCode, FailureReason: common.ScriptFailure}
	}
	defer file.Close()

	var codeStr string
	scanner := bufio.NewScanner(file)
	scanner.Split(bufio.ScanLines)
	for scanner.Scan() {
		codeStr = scanner.Text()
		break
	}

	bErrCode, err := strconv.Atoi(codeStr)
	if err != nil {
		return &ErrUnknownFailure{Inner: eerr, ExitCode: SystemFailureExitCode}
	}

	// We want to modify the exit code found in the error message to reflect the
	// true error as defined in the file. This aims to prevent confusion users
	// would like experience when presented with the exit status in the job log.
	return &common.BuildError{Inner: fmt.Errorf("exit status %s", codeStr), ExitCode: bErrCode, FailureReason: common.ScriptFailure}
```

**File:** executors/custom/api/const.go (L1-19)
```go
package api

const (
	// The name of the variable used to pass the value of Build failure exit code
	// that should be returned from Custom executor driver
	BuildFailureExitCodeVariable = "BUILD_FAILURE_EXIT_CODE"

	// The name of the variable used to pass the value of System failure exit code
	// that should be returned from Custom executor driver
	SystemFailureExitCodeVariable = "SYSTEM_FAILURE_EXIT_CODE"

	// The name of the variable used to pass the value of the path to an optional
	// file that the driver can use to provide a specific build failure code
	BuildCodeFileVariable = "BUILD_EXIT_CODE_FILE"

	// The name of the variable used to pass the value of path to the file that
	// contains JSON encoded content of job API received from GitLab's API
	JobResponseFileVariable = "JOB_RESPONSE_FILE"
)
```

**File:** executors/custom/api/config.go (L16-16)
```go
	JobEnv *map[string]string `json:"job_env,omitempty"`
```
