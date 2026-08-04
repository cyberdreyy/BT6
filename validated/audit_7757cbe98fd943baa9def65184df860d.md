### Title
Unsafe file open on driver-controlled `BUILD_EXIT_CODE_FILE` path allows symlink-based read outside build tempDir - ([File: executors/custom/command/command.go])

### Summary
`command.parseBuildFailure` opens `c.buildCodeFile` (the path exposed to the job/driver via `BUILD_EXIT_CODE_FILE`, i.e. `api.BuildCodeFileVariable`) with a plain `os.Open` and no symlink/containment check. If the driver-managed job environment can write to that path (which is the documented mechanism for reporting a custom exit code), a job script can replace the expected regular file with a symlink pointing outside `e.tempDir` before exiting with `BuildFailureExitCode`, causing the runner process to open and read the first line of an arbitrary host-side file.

### Finding Description
The path is created by the runner as `e.buildExitCodeFile = filepath.Join(e.tempDir, "build_exit_code")` [1](#0-0) , and is exposed to the driver/job process through the `BUILD_EXIT_CODE_FILE` environment variable (`api.BuildCodeFileVariable`) set in `command.New` [2](#0-1) . The documented usage pattern instructs the job script to `echo $exit_code > ${BUILD_EXIT_CODE_FILE}` before exiting with `BUILD_FAILURE_EXIT_CODE` — meaning job-controlled tooling is expected to create/overwrite this file, which is exactly the precondition stated in the question [3](#0-2) .

When `RunExec` exits with `BuildFailureExitCode`, `waitForCommand` calls `parseBuildFailure`, which does:
```go
file, err := os.Open(c.buildCodeFile)
```
with no `os.Lstat`, no `O_NOFOLLOW`, and no check that the resolved real path stays within `e.tempDir` [4](#0-3) . If job-controlled tooling replaces the file at that fixed path with a symlink to an arbitrary host path (e.g. `ln -sf /etc/somefile "$BUILD_EXIT_CODE_FILE"`), `os.Open` follows the symlink and the runner reads the first line of the target file via `bufio.Scanner` [5](#0-4) . There is no existing check anywhere in this path (in `custom.go` or `command.go`) that validates the file is a regular file or resides under `e.tempDir` before opening it.

### Impact Explanation
The runner process performs a file open/read on the host path that a symlink resolves to, outside `e.tempDir`, which violates the "file operations must stay within intended build root" invariant. The direct content is not sent to job logs verbatim; the only content that can be echoed back is via `strconv.Atoi(codeStr)` succeeding, which reflects at most the numeric value of the first line into the job's error message ("exit status %s") [6](#0-5) . Thus, the concrete impact is: (1) an out-of-tempDir file open/read is triggered by unprivileged job input (info-disclosure primitive limited mostly to whether the first line is a parseable integer), and (2) opening host paths under job influence can also produce side effects (e.g. blocking reads if pointed at a FIFO/special file), a minor denial-of-service vector for the runner-host process.

### Likelihood Explanation
This requires the driver/environment to actually map the `BUILD_EXIT_CODE_FILE` path into job-writable space — which is the documented, intended custom-executor mechanism for exit-code reporting, not an unusual or admin-misconfiguration case. Any pipeline author using a custom executor driver that honors this documented convention can trivially create a symlink instead of writing a plain integer, since the job script fully controls what is written at that path. The precondition is realistic for any custom executor setup following the documented usage.

### Recommendation
In `parseBuildFailure`, resolve `c.buildCodeFile` with `filepath.EvalSymlinks` (or open with `os.O_NOFOLLOW` where supported) and verify the resolved path is contained within `e.tempDir` before opening; reject and fall back to the default `BuildError`/`ScriptFailure` behavior if the path is a symlink or escapes the tempDir. Alternatively, `os.Lstat` the path first and refuse to open anything that is not a regular file.

### Proof of Concept
```go
func TestParseBuildFailure_RejectsSymlinkOutsideTempDir(t *testing.T) {
    tempDir := t.TempDir()
    outside := t.TempDir()
    secret := filepath.Join(outside, "secret")
    require.NoError(t, os.WriteFile(secret, []byte("999"), 0o600))

    buildCodeFile := filepath.Join(tempDir, "build_exit_code")
    require.NoError(t, os.Symlink(secret, buildCodeFile))

    cmd := &command{buildCodeFile: buildCodeFile}
    err := cmd.parseBuildFailure(&exec.ExitError{ProcessState: &os.ProcessState{}})

    // Expected (fixed) behavior: symlink escaping tempDir must be rejected,
    // not followed. Currently this test would fail because parseBuildFailure
    // follows the symlink and returns BuildError{ExitCode: 999}.
    var buildErr *common.BuildError
    require.ErrorAs(t, err, &buildErr)
    assert.NotEqual(t, 999, buildErr.ExitCode)
}
```

### Citations

**File:** executors/custom/custom.go (L111-111)
```go
	e.buildExitCodeFile = filepath.Join(e.tempDir, "build_exit_code")
```

**File:** executors/custom/command/command.go (L56-62)
```go
	defaultVariables := map[string]string{
		"TMPDIR":                          cmdOpts.Dir,
		api.BuildFailureExitCodeVariable:  strconv.Itoa(BuildFailureExitCode),
		api.SystemFailureExitCodeVariable: strconv.Itoa(SystemFailureExitCode),
		api.BuildCodeFileVariable:         options.BuildExitCodeFile,
		api.JobResponseFileVariable:       options.JobResponseFile,
	}
```

**File:** executors/custom/command/command.go (L120-127)
```go
func (c *command) parseBuildFailure(eerr *exec.ExitError) error {
	file, err := os.Open(c.buildCodeFile)
	if err != nil {
		// If the driver has not generated a file at the prescribed location
		// we revert to the default BuildError and exitCode.
		return &common.BuildError{Inner: eerr, ExitCode: BuildFailureExitCode, FailureReason: common.ScriptFailure}
	}
	defer file.Close()
```

**File:** executors/custom/command/command.go (L129-135)
```go
	var codeStr string
	scanner := bufio.NewScanner(file)
	scanner.Split(bufio.ScanLines)
	for scanner.Scan() {
		codeStr = scanner.Text()
		break
	}
```

**File:** executors/custom/command/command.go (L137-145)
```go
	bErrCode, err := strconv.Atoi(codeStr)
	if err != nil {
		return &ErrUnknownFailure{Inner: eerr, ExitCode: SystemFailureExitCode}
	}

	// We want to modify the exit code found in the error message to reflect the
	// true error as defined in the file. This aims to prevent confusion users
	// would like experience when presented with the exit status in the job log.
	return &common.BuildError{Inner: fmt.Errorf("exit status %s", codeStr), ExitCode: bErrCode, FailureReason: common.ScriptFailure}
```

**File:** docs/executors/custom.md (L486-495)
```markdown
You can optionally supply a file that contains the exit code when a build fails.
The expected path for the file is provided through the `BUILD_EXIT_CODE_FILE` environment
variable. For example:

```shell
if [ $exit_code -ne 0 ]; then
  echo $exit_code > ${BUILD_EXIT_CODE_FILE}
  exit ${BUILD_FAILURE_EXIT_CODE}
fi
```
```
