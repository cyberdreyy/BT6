### Title
Docker executor's `resumeDependencies()` resumes/attaches to containers referenced by an EnvironmentKey without verifying runner/job ownership - (File: executors/docker/docker.go)

### Summary
`executor.resumeDependencies()` parses the job's `EnvironmentKey` and immediately calls `ContainerInspect` on the embedded `build-container-id`/`helper-id`/`service-ids` and adopts them as the job's own containers, without ever checking that the `EnvironmentKey.RunnerID`/`SystemID` (or any project/job ownership label) matches the current runner/job. This contrasts with the autoscaler executor's `validateEnvKey`, which explicitly rejects keys not issued by the same runner/machine before trusting them.

### Finding Description
`executors/docker/docker.go` `resumeDependencies()` does: [1](#0-0) 
It calls `common.ParseEnvironmentKey(e.Build.EnvironmentKey())` and `parseEnvKeyFields(envKey.Fields)`, then directly does `e.dockerConn.ContainerInspect(e.Context, fields.buildContainerID)` / `...helperContainerID`, storing the result as `e.buildContainer`/`e.helperContainer` with no comparison of `envKey.RunnerID`/`envKey.SystemID` to the current runner's identity, and no check of the container's `com.gitlab.gitlab-runner.*` labels (job.id/project.id) that are otherwise applied per-container in `executors/docker/internal/labels/labels.go`.

By contrast, `executors/internal/autoscaler/executor.go`'s `validateEnvKey` explicitly enforces: [2](#0-1) 

Once `e.buildContainer`/`e.helperContainer` are adopted, subsequent job stages call `commandExecutor.requestBuildContainer()`, which reuses the adopted container via `hasExistingContainer` (since it inspects successfully) and proceeds to `runContainer()` → `startAndWatchContainer` → the exec path (`executors/docker/internal/exec/exec.go`) that attaches to the container and streams the job's script into it. There is no authorization gate between "container ID came from an externally supplied `EnvironmentKey`" and "container is trusted as this job's own build/helper container."

### Impact Explanation
If the `EnvironmentKey` embeds a container ID belonging to a different job/project's (still running or stopped-but-not-removed) build or helper container, `resumeDependencies()` adopts it as this job's own container, and the job's script/commands get executed inside that foreign container. Since the original project's container still has that job's environment variables (including masked/protected CI variables, `CI_JOB_TOKEN`, registry/cache credentials) present in its process environment or filesystem, the resuming attacker's script can read and exfiltrate them - a cross-project secret leak and cross-job takeover.

### Likelihood Explanation
Exploitability depends on preconditions not fully demonstrated as attacker-controllable from this repository alone: `spec.Job.SuspendOptions.EnvironmentKey` is populated by the GitLab coordinator/backend as part of the job payload sent to the runner (`common/build.go`'s `EnvironmentKey()`/`suspendEnvironment()` are the only producers found), not parsed from ordinary pipeline YAML or job-script variables that a normal pipeline author edits directly. In addition, even with a forged key, the attacker needs to know another project's live container ID - a random Docker-generated 64-char hex string - and that container must exist on the same Docker daemon/host the attacker's runner talks to (i.e., a shared-runner/shared-host scenario). These two facts reduce likelihood substantially versus a fully attacker-writable, guessable identifier. What is concretely established, however, is that the runner-side code has no defense-in-depth ownership check here at all, unlike the parallel autoscaler code path, meaning **if** the key or container ID is ever attacker-influenced (e.g., via a bug/leak in how the coordinator issues/returns this key, or key replay across pipelines), nothing in the docker executor stops cross-tenant container reuse.

### Recommendation
In `resumeDependencies()`, before trusting `fields.buildContainerID`/`helperContainerID`/`serviceContainerIDs`:
1. Validate `envKey.RunnerID` and `envKey.SystemID` against the current runner's ID/SystemID, mirroring `executors/internal/autoscaler/executor.go`'s `validateEnvKey`.
2. After `ContainerInspect`, verify the container's `com.gitlab.gitlab-runner.job.id` / `project.id` / `runner.local_id` labels (set in `executors/docker/internal/labels/labels.go`) match the resuming build's own job/project/runner identity before adopting it, rejecting resume if they don't match.

### Proof of Concept
Add a unit test in `executors/docker/docker_test.go` alongside `TestResumeDependencies`:
```go
func TestResumeDependencies_RejectsForeignContainer(t *testing.T) {
    c := docker.NewMockClient(t)
    e := executorWithMockClient(c)
    require.NoError(t, e.dockerConnector.Connect(t.Context(), common.ExecutorPrepareOptions{}, e))
    e.Config = common.RunnerConfig{}
    e.Config.Docker = &common.DockerConfig{}
    e.Build = &common.Build{
        Runner: &common.RunnerConfig{
            RunnerCredentials: common.RunnerCredentials{Token: "victim-runner-token"},
            RunnerID:          1,
        },
    }
    e.Build.Variables = append(e.Build.Variables,
        spec.Variable{Key: featureflags.SuspendableEnvironments, Value: "true"},
    )
    // Forged key: RunnerID does not belong to this runner/job.
    e.Build.Job.SuspendOptions.EnvironmentKey =
        "999/other-system/build-container-id=victim-build-cid&helper-id=victim-helper-cid"

    // Expect resumeDependencies to reject before ever calling ContainerInspect.
    err := e.resumeDependencies()
    require.Error(t, err)
    assert.Contains(t, err.Error(), "not issued by this runner")
    c.AssertNotCalled(t, "ContainerInspect", mock.Anything, "victim-build-cid")
}
```
Currently this test fails because `resumeDependencies()` proceeds straight to `ContainerInspect("victim-build-cid")` with no ownership check, confirming the missing authorization gate.

### Citations

**File:** executors/docker/docker.go (L1515-1536)
```go
func (e *executor) resumeDependencies() error {
	envKey, err := common.ParseEnvironmentKey(e.Build.EnvironmentKey())
	if err != nil {
		return err
	}
	fields, err := parseEnvKeyFields(envKey.Fields)
	if err != nil {
		return err
	}

	buildInspect, err := e.dockerConn.ContainerInspect(e.Context, fields.buildContainerID)
	if err != nil {
		return fmt.Errorf("build container %s not found: %w", fields.buildContainerID, err)
	}
	e.buildContainerID = buildInspect.ID
	e.buildContainer = &buildInspect

	helperInspect, err := e.dockerConn.ContainerInspect(e.Context, fields.helperContainerID)
	if err != nil {
		return fmt.Errorf("helper container %s not found: %w", fields.helperContainerID, err)
	}
	e.helperContainer = &helperInspect
```

**File:** executors/internal/autoscaler/executor.go (L190-201)
```go
func validateEnvKey(envKey string, runnerID int64, systemID string) (string, url.Values, error) {
	key, err := common.ParseEnvironmentKey(envKey)
	if err != nil {
		return "", nil, err
	}

	if key.RunnerID != runnerID {
		return "", nil, errors.New("environment key was not issued by this runner")
	}
	if key.SystemID != systemID {
		return "", nil, errors.New("environment key was not issued by this machine")
	}
```
