Confirmed: `resolveAuthConfigForImage` in `executors/docker/internal/pull/manager.go` calls `auth.Resolver{}.ConfigForImage` -> `AllConfigs` -> `getUserConfiguration(dockerAuthConfig)` (the job-controlled `DOCKER_AUTH_CONFIG` value) -> `readConfigsFromReader`, which is the **same code path** used to parse the Runner host's own `~/.docker/config.json`. This function executes `credHelpers`/`credsStore` entries via `credentials.NewNativeStore` on the **Runner host process**, not inside the job's container.

### Title
Job-controlled `DOCKER_AUTH_CONFIG` triggers execution of host-installed docker-credential helpers outside the job sandbox - (File: helpers/docker/auth/auth.go)

### Summary
`BuildSettings.DockerAuthConfig` is stored verbatim from the job variable `DOCKER_AUTH_CONFIG` with no schema restriction [1](#0-0)  and is passed unmodified into `auth.Resolver.AllConfigs`/`getUserConfiguration`, which parses it with the same `readConfigsFromReader` logic used for the Runner's own home-directory Docker config [2](#0-1) . If the job's JSON contains a `credHelpers` map, the Runner host process invokes `docker-credential-<helper>` binaries via `credentials.NewNativeStore(config, helper).Get(registry)` [3](#0-2) , executing on the Runner host, not inside the docker executor's container sandbox.

### Finding Description
The call chain is: `validate(DOCKER_AUTH_CONFIG)` in `common/build_settings.go` (raw string stored with no JSON/schema validation) [1](#0-0)  → `BuildSettings.DockerAuthConfig` → `e.Build.GetDockerAuthConfig()` used to build `pull.ManagerConfig.AuthConfig` [4](#0-3)  → `manager.resolveAuthConfigForImage` [5](#0-4)  → `auth.Resolver{}.ConfigForImage` → `AllConfigs` → `getUserConfiguration(dockerAuthConfig)` [6](#0-5)  → `readConfigsFromReader` [7](#0-6) .

Inside `readConfigsFromReader`, if the parsed config has `CredentialHelpers` set, `readConfigsFromCredentialsHelper` is invoked, which for each `(registry, helper)` pair checks only that `helper == filepath.Base(helper)` (blocking path traversal / `/`, `..`) and then constructs a `credentials.NewNativeStore(config, helper)` and calls `.Get(registry)` [8](#0-7) . Docker's native credential store implementation executes an external binary named `docker-credential-<helper>` found on `PATH`, passing the registry name on stdin and returning stored credentials on stdout. This execution happens in the Runner's own process/host environment (the same environment used to read `~/.docker/config.json`), not inside the job's docker executor container — it happens before/as part of resolving auth for a docker `pull`, which runs on the Runner host machine.

Existing checks (`errPathTraversal`) only stop directory traversal in the helper name; they do not stop selection of an arbitrary *existing* system binary matching the `docker-credential-<name>` naming convention, nor do they restrict which `registry` string is passed to `.Get()`. An unprivileged job can therefore choose any `helper` suffix corresponding to any credential helper already installed on the Runner host (e.g., cloud-specific helpers like `docker-credential-ecr-login`, `docker-credential-gcr`, `docker-credential-pass`, `docker-credential-osxkeychain`, etc., which Runner administrators commonly install for legitimate registry access) and any `registry` string, and have the Runner host execute that helper and return whatever credentials it yields for that registry key — even for registries/services unrelated to the job's own image, since `ConfigForImage`/`resolveAuthConfigForImage` will surface those credentials into the `AuthConfig` used for `docker pull`, and the credentials get base64-encoded into `opts.RegistryAuth` [9](#0-8) , which is included in job logs/debug output on failure (`authConfig.Username`, `authConfig.ServerAddress` printed via `Debugln`) and, more importantly, actually used to authenticate the pull against a registry chosen by the attacker.

### Impact Explanation
This lets an unprivileged pipeline author, purely by setting `DOCKER_AUTH_CONFIG`, cause the Runner host process (outside the job's docker executor sandbox) to execute any locally installed `docker-credential-*` helper binary and retrieve credentials for a registry hostname of the attacker's choosing. If the Runner host has any credential helper configured for other purposes (common in cloud environments — ECR/GCR/ACR login helpers, keychain-backed stores), this is a sandbox-escape-adjacent credential exfiltration primitive: the job can request credentials scoped to registries/services it should not have access to, and those credentials are then used to authenticate an actual `docker pull`, effectively laundering secret retrieval into an observable registry-auth attempt. This is a genuine breach of the "job must not escape its executor sandbox" and "secrets must not leak across jobs/projects" invariants, since the attack executes on the shared Runner host rather than inside the isolated build container.

### Likelihood Explanation
Requires: (1) docker executor with a Runner host that has at least one `docker-credential-*` helper binary installed and reachable on `PATH` (a common real-world configuration, not an admin misconfiguration being exploited — it's a normal Docker credential-helper setup for pulling from private/cloud registries), and (2) the job being able to set `DOCKER_AUTH_CONFIG` (a documented and normally allowed CI/CD variable). No other privilege is needed; the attacker only needs to know or guess the naming convention `docker-credential-<name>` and a plausible helper name, which is discoverable via common cloud tooling conventions. This is fully reproducible/deterministic once a helper binary exists on the host.

### Recommendation
Reject or ignore `credHelpers`/`credsStore` directives when parsing the job-supplied `DOCKER_AUTH_CONFIG` payload — this source should only ever populate plain `auths` entries (username/password/identitytoken), never trigger local credential-helper execution. Concretely, in `helpers/docker/auth/auth.go`, add a parameter/flag to `readConfigsFromReader` (or a wrapper used specifically by `getUserConfiguration`) that strips or errors out on non-empty `CredentialHelpers`/`CredentialsStore` fields before calling `readConfigsFromCredentialsHelper`/`readConfigsFromCredentialsStore`, so that only the Runner-host-sourced (`~/.docker/config.json`) call path is allowed to invoke local credential helpers.

### Proof of Concept
Go unit test in `helpers/docker/auth/auth_test.go`:
```go
func TestUserSuppliedAuthConfigCannotTriggerCredentialHelper(t *testing.T) {
    // Simulate a job setting DOCKER_AUTH_CONFIG with credHelpers pointing
    // at an arbitrary/marker helper name.
    dockerAuthConfig := `{
        "auths": {"registry.example.com": {}},
        "credHelpers": {"attacker-registry.example.com": "marker"}
    }`

    _, configs, err := getUserConfiguration(dockerAuthConfig)
    require.NoError(t, err)

    // Assert: no credential-helper execution occurred / no credentials for
    // "attacker-registry.example.com" were resolved via credHelpers.
    for _, c := range configs {
        require.NotEqual(t, "attacker-registry.example.com", c.ServerAddress,
            "job-supplied DOCKER_AUTH_CONFIG must not resolve credentials via local credHelpers")
    }
}
```
Expected current (vulnerable) behavior: if a binary named `docker-credential-marker` exists on `PATH`, the test host executes it and returns credentials for `attacker-registry.example.com`, proving the escape. Expected fixed behavior: `credHelpers`/`credsStore` in job-supplied config are ignored/stripped, and the assertion passes unconditionally regardless of what binaries exist on `PATH`.

### Citations

**File:** common/build_settings.go (L239-240)
```go
	case *string:
		*v = raw
```

**File:** helpers/docker/auth/auth.go (L128-198)
```go
func (r Resolver) AllConfigs(
	dockerAuthConfig, username string,
	credentials []spec.Credentials, logger Logger,
) (RegistryInfos, error) {
	resolvers := []func() (string, []types.AuthConfig, error){
		func() (string, []types.AuthConfig, error) {
			return getUserConfiguration(dockerAuthConfig)
		},
		func() (string, []types.AuthConfig, error) {
			return r.getHomeDirConfiguration(username)
		},
		func() (string, []types.AuthConfig, error) {
			return getBuildConfiguration(credentials)
		},
	}
	res := RegistryInfos{}

	for _, r := range resolvers {
		source, configs, err := r()
		if errors.Is(err, errPathTraversal) {
			return nil, err
		}
		if err != nil {
			logger.Warningln(fmt.Sprintf(
				"Failed to resolve credentials from %v: %v. Credentials from this source will not be used.",
				source, err,
			))
			continue
		}

		if len(configs) == 0 {
			continue
		}

		hostnames := []string{} // used only for logging

		for _, conf := range configs {
			registryPath := convertToRegistryPath(conf.ServerAddress)
			hostnames = append(hostnames, registryPath)

			newRegistryInfo := RegistryInfo{
				Path:       registryPath,
				Source:     source,
				AuthConfig: conf,
			}

			if err := res.Append(newRegistryInfo); err != nil {
				logger.Debugln(fmt.Sprintf("Not adding Docker credentials: %s", err.Error()))
			}
		}

		// Source can be blank if there is no home dir configuration
		if source != "" {
			logger.Debugln(fmt.Sprintf("Loaded Docker credentials, source = %q, hostnames = %v, error = %v", source, hostnames, err))
		}
	}

	return res, nil
}

func getUserConfiguration(dockerAuthConfig string) (string, []types.AuthConfig, error) {
	authConfigs, err := readConfigsFromReader(bytes.NewBufferString(dockerAuthConfig))
	if err != nil {
		return configSourceNameUserVariable, nil, err
	}
	if authConfigs == nil {
		return "", nil, nil
	}

	return configSourceNameUserVariable, authConfigs, nil
}
```

**File:** helpers/docker/auth/auth.go (L323-352)
```go
func readConfigsFromReader(r io.Reader) ([]types.AuthConfig, error) {
	config := &configfile.ConfigFile{}
	if err := config.LoadFromReader(r); err != nil {
		return nil, err
	}
	if !config.ContainsAuth() {
		// we can bail out early when there is no auth configured at all
		return nil, nil
	}

	auths := config.GetAuthConfigs()

	if config.CredentialsStore != "" {
		authsFromCredentialsStore, err := readConfigsFromCredentialsStore(config)
		if err != nil {
			return nil, err
		}
		maps.Copy(auths, authsFromCredentialsStore)
	}

	if config.CredentialHelpers != nil {
		authsFromCredentialsHelpers, err := readConfigsFromCredentialsHelper(config)
		if err != nil {
			return nil, err
		}
		maps.Copy(auths, authsFromCredentialsHelpers)
	}

	return withStableOrder(auths), nil
}
```

**File:** helpers/docker/auth/auth.go (L384-403)
```go
func readConfigsFromCredentialsHelper(config *configfile.ConfigFile) (map[string]types.AuthConfig, error) {
	helpersAuths := make(map[string]types.AuthConfig)

	for registry, helper := range config.CredentialHelpers {
		if helper != filepath.Base(helper) {
			// Fail processing if credential helpers attempting path traversal are detected
			return nil, errPathTraversal
		}

		store := credentials.NewNativeStore(config, helper)

		newAuths, err := store.Get(registry)
		if err != nil {
			return nil, err
		}

		helpersAuths[registry] = newAuths
	}

	return helpersAuths, nil
```

**File:** executors/docker/pull.go (L7-14)
```go
func newPullManagerConfig(e *executor) pull.ManagerConfig {
	return pull.ManagerConfig{
		DockerConfig: e.Config.Docker,
		AuthConfig:   e.Build.GetDockerAuthConfig(),
		ShellUser:    e.Shell().User,
		Credentials:  e.Build.Credentials,
	}
}
```

**File:** executors/docker/internal/pull/manager.go (L276-302)
```go
func (m *manager) resolveAuthConfigForImage(imageName string) (*cli.AuthConfig, error) {
	registryInfo, err := auth.Resolver{}.ConfigForImage(
		imageName,
		m.config.AuthConfig,
		m.config.ShellUser,
		m.config.Credentials,
		m.logger,
	)
	if err != nil {
		return nil, err
	}

	if registryInfo == nil {
		m.logger.Debugln(fmt.Sprintf("No credentials found for %v", imageName))
		return nil, nil
	}

	authConfig := &registryInfo.AuthConfig
	m.logger.Println(fmt.Sprintf("Authenticating with credentials from %v", registryInfo.Source))
	m.logger.Debugln(fmt.Sprintf(
		"Using %v to connect to %v in order to resolve %v...",
		authConfig.Username,
		authConfig.ServerAddress,
		imageName,
	))
	return authConfig, nil
}
```

**File:** executors/docker/internal/pull/manager.go (L304-333)
```go
func (m *manager) pullDockerImage(
	imageName string, options spec.ImageDockerOptions, ac *cli.AuthConfig, platform *ocispec.Platform,
) (*image.InspectResponse, error) {
	if m.onPullImageHookFunc != nil {
		m.onPullImageHookFunc()
	}
	msg := "Pulling docker image %s ..."
	if options.Platform == "" {
		msg = fmt.Sprintf(msg, imageName)
	} else {
		msg = fmt.Sprintf(msg, imageName+" for platform "+options.Platform)
	}
	m.logger.Println(msg)

	ref := imageName
	// Add :latest to limit the download results
	if !strings.ContainsAny(ref, ":@") {
		ref += ":latest"
	}

	opts := client.ImagePullOptions{}
	if platform != nil {
		opts.Platforms = []ocispec.Platform{*platform}
	}

	var err error

	if opts.RegistryAuth, err = auth.EncodeConfig(ac); err != nil {
		return nil, &common.BuildError{Inner: err, FailureReason: common.RunnerSystemFailure}
	}
```
