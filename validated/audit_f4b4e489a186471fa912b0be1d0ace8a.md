### Title
Command injection in `chown` command construction via unsanitized `GIT_CLONE_PATH`/build directory - ([File: executors/docker/docker_command.go])

### Summary
`commandExecutor.executeChownOnDir` (in `executors/docker/docker_command.go`, not `internal/exec/exec.go` as referenced — the function lives in `docker_command.go`) builds a shell command string with `fmt.Sprintf("chown -RP -- %d:%d %q", uid, gid, dir)` and feeds it as **stdin to a `bash` process** running inside the helper container, rather than passing `dir` as a discrete argv element. Because `dir` is quoted with Go's `%q` (Go string-literal quoting) instead of POSIX-shell quoting, and because the resulting string is interpreted by `bash` as a script, an attacker-controlled path containing `$(...)` survives inside the double quotes and is executed as a command substitution by bash.

### Finding Description
- `changeFilesOwnership` (executors/docker/docker_command.go:302) is only invoked when `FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR` is enabled (`isUmaskDisabled`, docker_command.go:82) and the build image's default user is non-root.
- `executeChown` → `executeChownOnDir` (docker_command.go:370-418) runs on both `s.Build.FullProjectDir()` and `s.Build.TmpProjectDir()`, which derive from `b.BuildDir` (`common/build.go` `FullProjectDir`/`TmpProjectDir`). `b.BuildDir` is set in `getCustomBuildDir` (`common/build.go:473-492`) from the job-controlled `GIT_CLONE_PATH` variable when `custom_build_dir` is enabled on the runner. The only validation performed is that the resolved path stays under `rootDir` (no `..` prefix) — no filtering of shell metacharacters like `$()`, backticks, or quotes.
- The command is built as `fmt.Sprintf("chown -RP -- %d:%d %q", uid, gid, dir)` (docker_command.go:403) and sent as `Stdin` to `dockerExec.Exec` (`executors/docker/internal/exec/exec.go:65-153`), which attaches to the helper container and streams stdin into it. When `isUmaskDisabled()` is true, the helper container's entrypoint command is `/bin/bash` (`getHelperImageCmd`, docker_command.go:196-205), meaning the piped string is executed **as a bash script**, not passed as a single argv token to `chown`.
- Go's `%q` verb escapes only Go string-literal special characters (quotes, backslashes, control characters) — it does **not** perform shell escaping. A value such as `GIT_CLONE_PATH=/builds/$(id>/tmp/pwned)` passes the `getCustomBuildDir` traversal check (it doesn't start with `..`) and is embedded verbatim inside the double quotes: `chown -RP -- 1000:1000 "/builds/$(id>/tmp/pwned)"`. Bash evaluates `$(...)` **even inside double quotes**, so this results in arbitrary command execution inside the helper container as the user bash runs as (root, since the helper container itself typically runs as root before `chown` drops files to the build user).
- Existing checks (`getCustomBuildDir`'s `..`-prefix check, `path.Clean`) only defend against path traversal outside the build root; they do nothing to prevent shell metacharacter injection, and the code path never uses proper argv-based exec or shell quoting/escaping for `dir`.

### Impact Explanation
An unprivileged pipeline author can achieve arbitrary command execution inside the Docker helper container (which typically runs as root and has access to `s.Build.FullProjectDir()`/cache/build volumes shared with the build container). This breaks the "file operations must stay within intended build/cache/artifact roots" and "no escape from executor sandbox" invariants — the attacker gains code execution beyond just filesystem paths, inside a container that also mounts the build volume, enabling manipulation of build state, injection into artifacts, or tampering that affects the build container's later steps sharing the same volume.

### Likelihood Explanation
Preconditions required, all attacker/job-controllable or plausible runner configuration (not an inherently insecure admin choice by itself):
1. Runner uses the Docker executor.
2. `custom_build_dir` feature is enabled on the runner (a common, documented, legitimate configuration option, not equivalent to privileged/docker.sock misconfiguration).
3. `FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR=true` — GitLab Runner feature flags are commonly settable via job/pipeline CI/CD variables, which would put this under attacker control as well.
4. Job specifies an image whose default user is non-root (fully attacker-controlled via the `image:` field).
5. Job sets `GIT_CLONE_PATH` to a value containing `$(...)`.

Given these, the exploit is deterministic and repeatable on every job run matching the preconditions.

### Recommendation
- Never build shell command strings by embedding untrusted path values with `%q`; either pass arguments as discrete argv elements to `chown` (avoiding a shell entirely) or perform proper POSIX-shell quoting (e.g., using a battle-tested shell-escaping function) before embedding into a script.
- Avoid piping constructed commands into `/bin/bash` via stdin when arguments include untrusted data; prefer `docker exec`-style argv invocation (`["chown", "-RP", "--", fmt.Sprintf("%d:%d", uid, gid), dir]`) so `dir` is never subject to shell interpretation.
- Additionally sanitize/validate `GIT_CLONE_PATH` (and any user-supplied path used in commands) to reject shell metacharacters, not just `..` traversal.

### Proof of Concept
Go unit test outline for `executors/docker/docker_command_test.go`:
```go
func TestExecuteChownOnDir_CommandInjectionViaGitClonePath(t *testing.T) {
    maliciousDir := `/builds/$(touch /tmp/pwned)`
    // Simulate getCustomBuildDir accepting this (no ".." prefix) and
    // FullProjectDir() returning maliciousDir.
    fakeDockerExec := &mockDocker{}
    s := &commandExecutor{ /* ... build with BuildDir = maliciousDir ... */ }

    err := s.executeChownOnDir(containerInspect, fakeDockerExec, 1000, 1000, maliciousDir)
    require.NoError(t, err)

    // Assert the exact bytes sent to Stdin: verify it embeds `$(...)` unescaped
    sentScript := fakeDockerExec.capturedStdin
    assert.Contains(t, sentScript, `"/builds/$(touch /tmp/pwned)"`)
    // In a real bash-in-container integration test, assert that
    // /tmp/pwned was created inside the helper container despite chown
    // targeting only the build directory path.
}
```
Integration-level PoC job:
```yaml
variables:
  FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR: "true"
  GIT_CLONE_PATH: '$CI_BUILDS_DIR/$(id > /tmp/pwned)'
image: some-non-root-user-image
script:
  - echo hi
```
Expected assertion: after job runs, `/tmp/pwned` exists inside the helper container (or command execution side effects are observable), proving the `chown` stdin script executed injected commands rather than merely operating on a literal (even if malformed) path.