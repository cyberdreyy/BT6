## Analysis

The vulnerability is real and directly reproducible from the existing code and test suite.

### Reachable path
`DOCKER_AUTH_CONFIG` (job/pipeline CI/CD variable) is passed straight into `Resolver.AllConfigs` → `getUserConfiguration(dockerAuthConfig)` → `readConfigsFromReader` → `readConfigsFromCredentialsStore`, all executing inside the `gitlab-runner` host process (not the job's container), since credential resolution happens before the image is pulled by the runner itself. [1](#0-0) [2](#0-1) 

### Root cause
`readConfigsFromCredentialsStore` only guards against path traversal (`filepath.Base` check), then calls `credentials.NewNativeStore(config, config.CredentialsStore).GetAll()`, which execs `docker-credential-<CredentialsStore>` resolved via `PATH` in the runner's own process: [3](#0-2) 

The same pattern exists for `credHelpers`: [4](#0-3) 

There is no allow-list restricting which helper *names* are permitted — only a check that the name doesn't contain path separators/traversal sequences. So any name that happens to correspond to an existing `docker-credential-<name>` binary on the runner host's `PATH` will be executed.

### Confirmed by the test suite
The existing tests explicitly validate this exact behavior:
- `"DOCKER_AUTH_CONFIG overrides credential store"` sets `credsStore` to a name resolvable on PATH (via `getValidCredentialHelperSuffix` and `prependToPath` pointing at `testdata`) and asserts the helper script actually executes and returns credentials. [5](#0-4) [6](#0-5) 
- Path traversal attempts (`../../usr/bin/sudo`) are explicitly blocked and tested. [7](#0-6) [8](#0-7) 
- Missing helper binaries only produce a warning and don't fail the job, confirming the exec attempt is made regardless. [9](#0-8) 

This confirms: whatever binary name is supplied via `credsStore`/`credHelpers`, if `docker-credential-<name>` exists anywhere on the runner host's `PATH`, it will be executed in the `gitlab-runner` host process — outside the job's container sandbox.

---

### Title
Unprivileged job-controlled `DOCKER_AUTH_CONFIG` `credsStore`/`credHelpers` name causes host-process execution of arbitrary PATH-resolved `docker-credential-*` binary - (File: helpers/docker/auth/auth.go)

### Summary
`readConfigsFromCredentialsStore` and `readConfigsFromCredentialsHelper` only validate that the `credsStore`/`credHelpers` value has no path-traversal components, then pass it directly to `credentials.NewNativeStore(...).GetAll()/Get()`, which execs `docker-credential-<name>` resolved via the runner host's `PATH`. Because `DOCKER_AUTH_CONFIG` is a job/pipeline-controlled CI/CD variable, an unprivileged pipeline author can name any `docker-credential-<X>` binary that happens to exist on the runner host's `PATH`, causing it to run in the `gitlab-runner` process on the host rather than inside the job's container.

### Finding Description
`getUserConfiguration` feeds the raw `DOCKER_AUTH_CONFIG` string into `readConfigsFromReader`, which parses it as a docker `config.json` and, if `CredentialsStore`/`CredentialHelpers` are set, calls `readConfigsFromCredentialsStore`/`readConfigsFromCredentialsHelper`. The only defense present is `helper != filepath.Base(helper)` (path-traversal rejection); there is no allow-list of permitted helper names and no restriction that only administrator-approved/pre-registered credential helpers can be invoked. `credentials.NewNativeStore` (docker/cli) execs `docker-credential-<name>` by `PATH` lookup, in the runner's own process context — this is not sandboxed by the job's container executor at all, since credential resolution for image pulls happens in the runner binary, not inside the job container.

### Impact Explanation
If any binary named `docker-credential-<X>` exists on the runner host's `PATH` (common examples: `docker-credential-ecr-login`, `docker-credential-pass`, `docker-credential-gcloud`, `docker-credential-osxkeychain`, custom internal helpers installed by ops teams), an unprivileged job can force the runner host process to invoke it by simply setting `DOCKER_AUTH_CONFIG={"credsStore":"<X>"}`. This can be used to trigger unintended credential retrieval (potentially exfiltrating registry credentials configured for other purposes on the host) or, if a poorly-written/vulnerable helper is present, to trigger unexpected host-side behavior — all outside the job's sandbox and attributable to the runner process rather than the job container.

### Likelihood Explanation
Preconditions: the runner host must already have some `docker-credential-<name>` binary on `PATH` (this is common on runners configured with any cloud registry integration, e.g. ECR/GCR helpers, or `docker login` history). No admin cooperation or leaked secret is required beyond the attacker being an ordinary pipeline author able to set `DOCKER_AUTH_CONFIG` (a documented, user-settable CI/CD variable). The behavior is fully deterministic and repeatable — the existing test suite in `auth_test.go` already demonstrates the exact exec-by-name mechanism working end-to-end.

### Recommendation
Restrict `credsStore`/`credHelpers` values processed from job/pipeline-controlled sources (`DOCKER_AUTH_CONFIG`) to an explicit administrator-configured allow-list (e.g. only trust `credsStore`/`credHelpers` from `~/.docker/config.json` on the host, and disable/ignore these fields entirely when parsed from `DOCKER_AUTH_CONFIG`), since honoring arbitrary externally-resolved helper binaries defeats the purpose of per-job auth isolation.

### Proof of Concept
Go unit test in `helpers/docker/auth`:
1. Create a `testdata`-style directory containing a script `docker-credential-marker` that writes a sentinel file (e.g. `/tmp/pwned`) and returns valid JSON credentials, mirroring the existing `bin.sh`/`windows.cmd` helper fixtures.
2. Prepend that directory to `PATH` via `t.Setenv`, mirroring `prependToPath` in `auth_test.go`.
3. Call `Resolver{}.AllConfigs(`{"credsStore":"marker"}`, "", nil, logger)` (equivalent to `getUserConfiguration` → `readConfigsFromReader` → `readConfigsFromCredentialsStore`).
4. Assert the sentinel file was created (proving host-process execution occurred outside any container) and that no error/path-traversal rejection stopped it, in contrast to the traversal-attempt test cases which already assert `errPathTraversal`.

### Citations

**File:** helpers/docker/auth/auth.go (L188-198)
```go
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

**File:** helpers/docker/auth/auth.go (L369-382)
```go
func readConfigsFromCredentialsStore(config *configfile.ConfigFile) (map[string]types.AuthConfig, error) {
	if config.CredentialsStore != filepath.Base(config.CredentialsStore) {
		// Fail processing if credential store attempting path traversal are detected
		return nil, errPathTraversal
	}

	store := credentials.NewNativeStore(config, config.CredentialsStore)
	newAuths, err := store.GetAll()
	if err != nil {
		return nil, err
	}

	return newAuths, nil
}
```

**File:** helpers/docker/auth/auth.go (L384-404)
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
}
```

**File:** helpers/docker/auth/auth_test.go (L230-248)
```go
		"DOCKER_AUTH_CONFIG overrides credential store": {
			dockerAuthValue: fmt.Sprintf(`{"credsStore" : "%s"}`, getValidCredentialHelperSuffix(t)),
			image:           imageRegistryDomain2,
			checks: func(t *testing.T, result *RegistryInfo, err error, homeDir string, logger *fakeLogger) {
				authConfig := registryScriptConfig
				authConfig.ServerAddress = "https://registry2.domain.tld:5005/v1/"

				expectedResult := &RegistryInfo{
					Path:       "registry2.domain.tld:5005",
					Source:     configSourceNameUserVariable,
					AuthConfig: authConfig,
				}
				assert.NoError(t, err)
				assert.Equal(t, expectedResult, result)

				logger.ExpectLogs(t, [][]any{
					{`Loaded Docker credentials, source = "$DOCKER_AUTH_CONFIG", hostnames = [registry2.domain.tld:5005], error = <nil>`},
				})
			},
```

**File:** helpers/docker/auth/auth_test.go (L320-332)
```go
		"DOCKER_AUTH_CONFIG with missing credsStore binary logs warning and continues": {
			dockerAuthValue: `{"credsStore": "nonexistent-helper"}`,
			image:           imageRegistryDomain1,
			checks: func(t *testing.T, result *RegistryInfo, err error, homeDir string, logger *fakeLogger) {
				assert.Nil(t, result)
				assert.NoError(t, err)
				logger.ExpectLogs(t, nil)
				require.Len(t, logger.warningLogs, 1)
				warnMsg := fmt.Sprint(logger.warningLogs[0]...)
				assert.Contains(t, warnMsg, configSourceNameUserVariable)
				assert.Contains(t, warnMsg, "nonexistent-helper")
			},
		},
```

**File:** helpers/docker/auth/auth_test.go (L348-352)
```go
	dir, err := os.Getwd()
	require.NoError(t, err)

	// Prepend testdata directory to PATH so that docker-credential-* scripts are picked up
	prependToPath(t, filepath.Join(dir, "testdata"))
```

**File:** helpers/docker/auth/auth_test.go (L646-661)
```go
// getPathWithPathTraversalAttempt returns a relative path to an executable which exists on the host
// OS, to test path traversal attempts in credential helpers
func getPathWithPathTraversalAttempt(t *testing.T) string {
	dir, err := os.Getwd()
	require.NoError(t, err)

	credHelperPath, err := filepath.Rel(dir, `/usr/bin/sudo`)
	if runtime.GOOS == "windows" {
		credHelperPath, err = filepath.Rel(dir, `C:\Windows\notepad.exe`)
		credHelperPath = strings.ReplaceAll(credHelperPath, `\`, `\\`)
	}

	require.NoError(t, err)

	return credHelperPath
}
```

**File:** executors/docker/internal/pull/manager_test.go (L1162-1178)
```go
func TestResolveAuthConfigForImageErrorsOnPathTraversal(t *testing.T) {
	loggerMock := newMockPullLogger(t)
	loggerMock.On("Debugln", mock.Anything, mock.Anything, mock.Anything).Maybe()

	m := &manager{
		context: t.Context(),
		logger:  loggerMock,
		config: ManagerConfig{
			DockerConfig: &common.DockerConfig{},
			AuthConfig:   `{"credsStore": "../../usr/bin/sudo"}`,
		},
	}

	authConfig, err := m.resolveAuthConfigForImage("registry.domain.tld:5005/image/name:version")
	assert.ErrorContains(t, err, "path traversal")
	assert.Nil(t, authConfig)
}
```
