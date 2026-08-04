### Title
Attacker-controlled service containers share the "scripts"/"logs" emptyDir with the build container, letting a malicious service tamper with the stage script and exit-status log that `runInContainer` trusts - (File: executors/kubernetes/kubernetes.go)

### Summary
`runInContainer` (attach flow) executes a fixed shell command in the build container that reads and runs the stage script from `s.scriptPath(cmd.Stage)`, and determines success/failure from log-file exit-status markers written to `s.logFile()`. Both paths live on the `scripts` and `logs` `emptyDir` volumes, which `getVolumeMounts()` mounts, unconditionally and without isolation, into **every** container built via `buildContainer()` — including user-defined service containers (`opts.isServiceContainer`).

### Finding Description
- `saveScriptOnEmptyDir` writes the stage script to a fully deterministic, per-job (but not per-container) path: `scriptPath = fmt.Sprintf("%s/%s", s.scriptsDir(), scriptName)` [1](#0-0) , and `scriptPath`/`scriptsDir` are computed purely from project/job IDs, not from any per-container secret: [2](#0-1) .
- `getContainerInfo` builds the command the build container's persistent shell executes, which directly references `s.scriptPath(cmd.Stage)` to read and run the script content, and `s.logFile()` (under the shared `logs` emptyDir) is where the exit-status JSON marker (`command_exit_code`) is expected/parsed to determine success/failure: [3](#0-2) .
- `runInContainer` itself only opens the attach stream and then blocks on `s.remoteProcessTerminated`, trusting whatever `*exitStatus.CommandExitCode` value ultimately shows up on that channel (fed by the log processor reading `s.logFile()`): [4](#0-3) .
- Crucially, `buildContainer()` — the function used to construct **both** the build container and every service container (`opts.isServiceContainer` is only used to pick env vars) — mounts `s.getVolumeMounts()` verbatim: [5](#0-4) .
- `getVolumeMounts()` unconditionally appends the shared `scripts` volume at `s.scriptsDir()` and the shared `logs` volume at `s.logsDir()` for the calling container, with no per-container scoping, no read-only flag, and no way to exclude service containers from these two built-in mounts (only the Concrete/"steps" mode explicitly special-cases this by using a separate `stepsVolumeMounts()` that omits `logs`): [6](#0-5) . The Concrete-mode comment even documents the underlying fact that "service containers — which go through this shared `getVolumeMounts` path" would otherwise also reference `logs`: [7](#0-6) .
- Tests confirm this behavior for the legacy/attach (non-Concrete) execution strategy: with `FF_USE_LEGACY_KUBERNETES_EXECUTION_STRATEGY=false`, both `scripts` and `logs` mounts are present, and the same `getVolumeMounts()` path is shared by build and service containers ("non-steps attach ... wantScripts: true, wantLogs: true") [8](#0-7) , while the Concrete/steps mode deliberately built a separate `stepsServiceContainer` path specifically to stop services from getting the `logs` mount, with an explicit test asserting "service container must NOT mount the logs emptyDir" [9](#0-8)  — implying the underlying legacy/attach path does NOT have this protection for `logs`, and neither mode restricts `scripts` from service containers at all.

Exploit flow (legacy attach mode, `FF_USE_LEGACY_KUBERNETES_EXECUTION_STRATEGY=false`, which is the default non-Concrete strategy):
1. Attacker (pipeline author) defines a job with an arbitrary `services:` image whose entrypoint/command races to write into the shared, well-known path `s.scriptsDir()/<stage>.sh` (deterministic given project/job ID and shell extension) and/or `s.logsDir()/output.log`.
2. When the runner calls `saveScriptOnEmptyDir` and then `runInContainer`, the build container's shell reads and executes whatever bytes currently occupy `scriptPath` — since this is a plain shared filesystem path, nothing stops the service container from overwriting it, appending to it, or racing the write before the build container consumes it.
3. Because `checkScriptExecution`/`remoteProcessTerminated` completion is driven purely by content parsed out of the shared `logFile()`, a malicious service can inject its own fabricated `"command_exit_code"` JSON marker into that same file, causing the runner to believe a stage exited with an attacker-chosen exit code (spoofing success or failure) or to desynchronize stage tracking (`s.remoteStageStatus`).
4. None of the existing checks (`verifyAllowedImages`, pull policy, proxy pool aliasing) validate or isolate the *content* of these shared paths — they only govern which images/ports are allowed, not filesystem isolation between build and service containers on the emptyDir volumes.

### Impact Explanation
A pipeline author can already control what their own build script does and can already fully control service images, so the primary novel impact is **script/log integrity manipulation across the build/service boundary that the runner's control-flow logic implicitly trusts**: injecting a forged exit-status marker can desynchronize `checkScriptExecution`'s stage-tracking, potentially causing retried stages to be skipped or misreported, or causing `runInContainer` to falsely report success/failure independent of what the actual build container executed. This is "stronger-context execution or output tampering" as scoped, but the blast radius is confined to the attacker's own job (the emptyDir is per-job, not shared across jobs or projects), so it does not break cross-tenant isolation.

### Likelihood Explanation
Fully reachable by an unprivileged pipeline author defining a `services:` entry with a custom image/command — no special runner configuration is required beyond the default (non-Concrete) Kubernetes execution strategy, which is common. The race/overwrite is straightforward to trigger since paths are fully deterministic and shared, though winning races against the exact moment the build container reads the script requires some timing control, which the attacker also controls via job script sleep primitives, service startup ordering, and `sleep`/health-check hooks in the service entrypoint.

### Recommendation
Do not mount the `scripts`/`logs` emptyDir volumes into service containers at all — mirror the Concrete-mode approach (`stepsVolumeMounts`) by giving service containers a distinct, minimal volume-mount set that excludes `scripts` and `logs`, or mount them read-only for service containers and use per-container sub-paths so a service cannot write into the build container's script/log files.

### Proof of Concept
Go integration test plan (in `executors/kubernetes`):
1. Build a fake/mocked pod with `FF_USE_LEGACY_KUBERNETES_EXECUTION_STRATEGY=false`, one build container and one service container defined via `s.buildContainer(containerBuildOpts{isServiceContainer:true, ...})`.
2. Assert (like the existing `TestGetVolumeMounts_PresenceAndPaths`/service-mount tests) that the resulting service container's `VolumeMounts` includes `scripts` and `logs` at the exact same `MountPath` as the build container — proving path collision.
3. Extend with a runtime PoC job: a `services:` container whose command does `while true; do echo '{"command_exit_code": 0}' >> /logs-*/output.log; sleep 0.1; done &` combined with overwriting `/scripts-*/step_script.sh`, then observe via `checkScriptExecution`/log-processor behavior that the runner's stage tracking is fed attacker-controlled markers.
   - Assertion: the runner's reported exit code/stage transition does not match what the build container's script actually executed, demonstrating output tampering originating from the service container across the shared volume.

### Citations

**File:** executors/kubernetes/kubernetes.go (L1147-1208)
```go
func (s *executor) getContainerInfo(cmd common.ExecutorCommand) (string, []string) {
	var containerCommand []string

	containerName := buildContainerName
	if cmd.Predefined {
		containerName = helperContainerName
	}

	shell := s.Shell().Shell

	switch shell {
	case shells.SNPwsh, shells.SNPowershell:
		// Translates to roughly "/path/to/parse_pwsh_script.ps1 /path/to/stage_script"
		containerCommand = []string{
			s.scriptPath(pwshJSONTerminationScriptName),
			s.scriptPath(cmd.Stage),
			s.buildRedirectionCmd(shell),
		}
	default:
		// Translates to roughly "sh -c '(/detect/shell/path.sh /stage/script/path.sh 2>&1 | tee) &'"
		// which when the detect shell exits becomes something like "bash /stage/script/path.sh".
		// This works unlike "gitlab-runner-build" since the detect shell passes arguments with "$@"
		containerCommand = []string{
			"sh",

			// We have to run the command in a background subshell. Unfortunately,
			// explaining why in a comment fails the code quality check of
			// function length not exceeding 60 lines, so `git blame` this instead.
			"-c",
			// The exit-status JSON marker that forwardLogLine/remoteProcessTerminated waits
			// for is normally printed by a trap *inside* the stage script itself (see
			// bashJSONTerminationScript). If the detect shell can't even open that script
			// (e.g. it's missing from the emptyDir), the trap never installs and no marker
			// is ever produced, so the runner would wait forever. Capture the exit code of
			// the detect-shell invocation from outside and print a fallback marker whenever
			// it comes back non-zero - the trap always exits 0 when it did get to run, so
			// the two cases can't collide. The fallback marker includes the same "script"
			// field the trap would have printed so StageCommandStatus.BuildStage() can still
			// identify the stage - otherwise a concurrent network error on the attach call
			// would make checkScriptExecution fail to match this stage and retry needlessly.
			fmt.Sprintf(fallbackExitStatusMarkerFmt,
				s.scriptPath(detectShellScriptName),
				s.scriptPath(cmd.Stage),
				s.scriptPath(cmd.Stage),
				s.buildRedirectionCmd(shell),
			),
		}
		if cmd.Predefined {
			// We use redirection here since the "gitlab-runner-build" helper doesn't pass input args
			// to the shell it executes, so we technically pass the script to the stdin of the underlying shell
			// translates roughly to "gitlab-runner-build <<< /stage/script/path.sh"
			containerCommand = append( //nolint:gocritic
				s.helperImageInfo.Cmd,
				"<<<",
				s.scriptPath(cmd.Stage),
				s.buildRedirectionCmd(shell),
			)
		}
	}

	return containerName, containerCommand
}
```

**File:** executors/kubernetes/kubernetes.go (L1427-1438)
```go
func (s *executor) saveScriptOnEmptyDir(ctx context.Context, scriptName, containerName, script string) error {
	shell, err := s.retrieveShell()
	if err != nil {
		return err
	}

	scriptPath := fmt.Sprintf("%s/%s", s.scriptsDir(), scriptName)
	saveScript, err := shell.GenerateSaveScript(*s.Shell(), scriptPath, script)
	if err != nil {
		return err
	}
	s.BuildLogger.Debugln(fmt.Sprintf("Saving stage script %s on Container %q", saveScript, containerName))
```

**File:** executors/kubernetes/kubernetes.go (L1549-1609)
```go
func (s *executor) buildContainer(opts containerBuildOpts) (api.Container, error) {
	var envVars []spec.Variable

	if opts.isServiceContainer {
		envVars = s.getServiceVariables(opts.imageDefinition)
	} else if opts.name == buildContainerName {
		envVars = s.Build.GetAllVariables().PublicOrInternal()
	}

	err := s.verifyAllowedImages(opts)
	if err != nil {
		return api.Container{}, err
	}

	containerPorts := make([]api.ContainerPort, len(opts.imageDefinition.Ports))
	proxyPorts := make([]proxy.Port, len(opts.imageDefinition.Ports))

	for i, port := range opts.imageDefinition.Ports {
		proxyPorts[i] = proxy.Port{Name: port.Name, Number: port.Number, Protocol: port.Protocol}
		containerPorts[i] = api.ContainerPort{ContainerPort: int32(port.Number)}
	}

	if len(proxyPorts) > 0 {
		aliases := opts.imageDefinition.Aliases()
		if len(aliases) == 0 {
			if opts.name != buildContainerName {
				aliases = []string{fmt.Sprintf("proxy-%s", opts.name)}
			} else {
				aliases = []string{opts.name}
			}
		}

		for _, serviceName := range aliases {
			s.ProxyPool[serviceName] = s.newProxy(serviceName, proxyPorts)
		}
	}

	pullPolicy, err := s.pullManager.GetPullPolicyFor(opts.name)
	if err != nil {
		return api.Container{}, err
	}

	command, args := s.getCommandAndArgs(opts.imageDefinition, opts.command...)

	container := api.Container{
		Name:            opts.name,
		Image:           opts.image,
		ImagePullPolicy: pullPolicy,
		Command:         command,
		Args:            args,
		Env:             buildVariables(envVars),
		Resources:       api.ResourceRequirements{Limits: opts.limits, Requests: opts.requests},
		Ports:           containerPorts,
		VolumeMounts:    s.getVolumeMounts(),
		SecurityContext: opts.securityContext,
		Lifecycle:       s.prepareLifecycleHooks(),
		Stdin:           true,
	}

	return container, nil
}
```

**File:** executors/kubernetes/kubernetes.go (L1674-1696)
```go
func (s *executor) logsDir() string {
	return s.baseDir(defaultLogsBaseDir,
		s.Config.Kubernetes.LogsBaseDir, s.Build.JobInfo.ProjectID, s.Build.Job.ID)
}

func (s *executor) scriptsDir() string {
	return s.baseDir(defaultScriptsBaseDir,
		s.Config.Kubernetes.ScriptsBaseDir, s.Build.JobInfo.ProjectID, s.Build.Job.ID)
}

func (s *executor) baseDir(defaultBaseDir, configDir string, projectId, jobId int64) string {
	baseDir := defaultBaseDir
	if configDir != "" {
		// if path ends with one or more / or \, drop it
		configDir = strings.TrimRight(configDir, "/\\")
		baseDir = configDir + defaultBaseDir
	}
	return fmt.Sprintf("%s-%d-%d", baseDir, projectId, jobId)
}

func (s *executor) scriptPath(stage common.BuildStage) string {
	return path.Join(s.scriptsDir(), s.scriptName(string(stage)))
}
```

**File:** executors/kubernetes/kubernetes.go (L1708-1757)
```go
func (s *executor) getVolumeMounts() []api.VolumeMount {
	var mounts []api.VolumeMount

	// scripts volumes are needed when using the Kubernetes executor in attach mode
	// FF_USE_LEGACY_KUBERNETES_EXECUTION_STRATEGY = false
	// or when the dumb init is used as it is copied from the helper to this volume
	if s.Build.IsFeatureFlagOn(featureflags.UseDumbInitWithKubernetesExecutor) ||
		!s.Build.IsFeatureFlagOn(featureflags.UseLegacyKubernetesExecutionStrategy) {
		mounts = append(
			mounts,
			api.VolumeMount{
				Name:      "scripts",
				MountPath: s.scriptsDir(),
			})
	}

	if !s.Build.IsFeatureFlagOn(featureflags.UseLegacyKubernetesExecutionStrategy) {
		// These volume mounts **MUST NOT** be mounted inside another volume mount.
		// E.g. mounting them inside the "repo" volume mount will cause the whole volume
		// to be owned by root instead of the current user of the image. Something similar
		// is explained here https://github.com/kubernetes/kubernetes/issues/2630#issuecomment-64679120
		// where the first container determines the ownership of a volume. However, it seems like
		// when mounting a volume inside another volume the first container or the first point of contact
		// becomes root, regardless of SecurityContext or Image settings changing the user ID of the container.
		// This causes builds to stop working in environments such as OpenShift where there's no root access
		// resulting in an inability to modify anything inside the parent volume.
		//
		// In Concrete (native steps) mode the logs emptyDir is not created (stepsVolumes
		// omits it because there is no helper container to consume it), so service
		// containers — which go through this shared getVolumeMounts path — must not
		// reference it either, or kube rejects the pod spec with an unknown-volume error.
		mounts = append(
			mounts,
			api.VolumeMount{
				Name:      "logs",
				MountPath: s.logsDir(),
			})
	}

	mounts = append(mounts, s.getVolumeMountsForConfig()...)

	if s.isDefaultBuildsDirVolumeRequired() {
		mounts = append(mounts, api.VolumeMount{
			Name:      "repo",
			MountPath: s.AbstractExecutor.RootDir(),
		})
	}

	return mounts
}
```

**File:** executors/kubernetes/kubernetes.go (L3232-3278)
```go
func (s *executor) runInContainer(
	ctx context.Context,
	stage common.BuildStage,
	name string,
	command []string,
) <-chan error {
	errCh := make(chan error, 1)
	go func() {
		defer close(errCh)

		attach := AttachOptions{
			PodName:       s.pod.Name,
			Namespace:     s.pod.Namespace,
			ContainerName: name,
			Command:       command,

			Config:     s.kubeConfig,
			KubeClient: s.kubeClient,
			Executor:   &DefaultRemoteExecutor{},

			Context: ctx,
		}

		kubeRequest := retry.WithFn(s, func() error {
			err := attach.Run()
			s.BuildLogger.Debugln(fmt.Sprintf("Trying to execute stage %v, got error %v", stage, err))
			return s.checkScriptExecution(stage, err)
		})

		if err := kubeRequest.Run(); err != nil {
			errCh <- err
		}

		exitStatus := <-s.remoteProcessTerminated
		s.BuildLogger.Debugln("Remote process exited with the status:", exitStatus)

		// CommandExitCode is guaranteed to be non nil when sent over the remoteProcessTerminated channel
		if *exitStatus.CommandExitCode == 0 {
			errCh <- nil
			return
		}

		errCh <- &commandTerminatedError{exitCode: *exitStatus.CommandExitCode}
	}()

	return errCh
}
```

**File:** executors/kubernetes/steps_pod_test.go (L295-330)
```go
// Concrete builds its own service containers without the legacy
// buildContainer, so a service container must mount the Concrete volume
// set (scripts emptyDir) and never the logs emptyDir, while still carrying
// its image, declared ports, and a registered session proxy.
func TestStepsServiceContainers_UseStepsMountsAndRegisterProxies(t *testing.T) {
	ex := newStepsTestExecutor(t)
	ex.AbstractExecutor.ProxyPool = proxy.NewPool()
	ex.options.Services = map[string]*spec.Image{
		"db": {Name: "postgres:15", Ports: []spec.Port{{Number: 5432, Name: "sql"}}},
	}

	containers, err := ex.stepsServiceContainers()
	require.NoError(t, err)
	require.Len(t, containers, 1)

	c := containers[0]
	assert.Equal(t, "db", c.Name)
	assert.Equal(t, "postgres:15", c.Image)

	var sawScripts, sawLogs bool
	for _, m := range c.VolumeMounts {
		switch m.Name {
		case "scripts":
			sawScripts = true
		case "logs":
			sawLogs = true
		}
	}
	assert.True(t, sawScripts, "service container must mount the scripts emptyDir")
	assert.False(t, sawLogs, "service container must NOT mount the logs emptyDir (Concrete has none)")

	require.Len(t, c.Ports, 1)
	assert.Equal(t, int32(5432), c.Ports[0].ContainerPort)
	_, ok := ex.ProxyPool["proxy-db"]
	assert.True(t, ok, "declared service ports must register a session proxy")
}
```

**File:** executors/kubernetes/steps_pod_test.go (L565-586)
```go
	rows := []matrixRow{
		{
			name:           "non-steps attach (FF_CONCRETE off, FF_USE_LEGACY off)",
			useNativeSteps: false, legacyFF: false,
			wantScripts: true, wantLogs: true, // ← regression canary
		},
		{
			name:           "non-steps legacy (FF_CONCRETE off, FF_USE_LEGACY on)",
			useNativeSteps: false, legacyFF: true,
			wantScripts: false, wantLogs: false,
		},
		{
			name:           "native-steps attach (FF_CONCRETE on, FF_USE_LEGACY off)",
			useNativeSteps: true, legacyFF: false,
			wantScripts: true, wantLogs: true, // native-steps-neutral: guard removed
		},
		{
			name:           "native-steps legacy (FF_CONCRETE on, FF_USE_LEGACY on)",
			useNativeSteps: true, legacyFF: true,
			wantScripts: false, wantLogs: false,
		},
	}
```
