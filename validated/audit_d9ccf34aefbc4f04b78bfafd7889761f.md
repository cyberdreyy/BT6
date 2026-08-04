### Title
Service alias collision with reserved container names lets a pipeline author overwrite the build/helper/init-container pull-policy entry - ([File: executors/kubernetes/kubernetes.go])

### Summary
`preparePullManager` seeds `dockerPullPoliciesPerContainer` with fixed keys (`buildContainerName`, `helperContainerName`, `initPermissionContainerName`, and conditionally `stepsBootstrapInitContainerName`), then iterates `s.options.Services` and does `dockerPullPoliciesPerContainer[containerName] = service.PullPolicies`. `s.options.Services` is keyed by `getServiceName`, which uses the user-supplied service `alias` verbatim as the container name whenever it is a valid DNS-1123 label not already claimed by another service — with no exclusion list for the reserved container names. A pipeline author can therefore define a `service` with `alias: helper` (or `build`, `init-permissions`, `init-steps-bootstrap`) and a per-service `pull_policy`, causing that service's attacker-chosen pull policy to silently overwrite the entry originally derived from `s.options.Image.PullPolicies` for the reserved container.

### Finding Description
- `prepareOptions` builds `s.options.Services` via `getServiceDefinition` → `getServiceName`, which returns the raw alias if it's a valid DNS label and not already used: [1](#0-0) 
- No check anywhere in this path excludes the reserved names `build`, `helper`, `init-permissions`, or `init-steps-bootstrap` from being chosen as a service's container name.
- `preparePullManager` first assigns the build image's pull policies to the reserved container-name keys, then unconditionally overwrites `dockerPullPoliciesPerContainer[containerName]` for every entry in `s.options.Services`, including any collision with a reserved key: [2](#0-1) 
- The resulting map is what gets converted per-container into `k8sPullPoliciesPerContainer` and handed to `pull.NewPullManager`, so the pull-policy cursor used later by `GetPullPolicyFor(helperContainerName)` (or `buildContainerName`, etc.) reflects the attacker's service-level `pull_policy`, not the runner/build-image configured one: [3](#0-2) 
- `ComputeEffectivePullPolicies` itself is not at fault — it correctly intersects whatever `pullPolicies` it's given against `allowedPullPolicies`: [4](#0-3) . The bug is upstream: the *wrong* per-container pull-policy list reaches it because the map key collided.

### Impact Explanation
This breaks the per-container pull-policy isolation invariant: a pipeline author can force the helper (or build, or init-permissions/init-steps-bootstrap) container's effective pull policy to whatever the author chooses for their own declared service, as long as it's within `allowed_pull_policies`. Concretely this could downgrade the helper/init container's pull policy from `Always` to `Never`/`IfNotPresent`, which only matters if a stale or substituted image with the same reference already exists locally on the node — a scenario that depends on node/image-cache state outside this job's control and overlaps with the excluded "malicious peers/nodes" category. Within this job's own pod alone (no external node-cache assumption), the concrete effect is limited to unauthorized pull-policy *value* substitution for a reserved container, not a demonstrated arbitrary image substitution.

### Likelihood Explanation
Highly reachable: it only requires `kubernetes` executor, native or legacy strategy, `UseNativeSteps()` not required (`buildContainerName`/`helperContainerName`/`initPermissionContainerName` are always seeded), and a `.gitlab-ci.yml` service with `alias: helper` (or `build`) plus a `pull_policy`. No special runner configuration beyond enabling per-image `pull_policy` and `allowed_pull_policies` is needed. This is fully attacker(pipeline-author)-controlled input via standard CI YAML.

### Recommendation
In `getServiceName` (or `getServiceDefinition`/`prepareOptions`), reject or fall back to the `svc-N` naming scheme when a service alias equals any reserved container name (`buildContainerName`, `helperContainerName`, `initPermissionContainerName`, `stepsBootstrapInitContainerName`). Alternatively, in `preparePullManager`, iterate services first and reserved containers last (or explicitly guard against overwriting reserved keys) so a service can never clobber a reserved container's pull-policy entry.

### Proof of Concept
Go unit test in `executors/kubernetes/kubernetes_test.go`:
```go
func TestPreparePullManager_ServiceAliasCannotOverrideReservedContainer(t *testing.T) {
    e := newExecutor()
    e.Config.Kubernetes = &common.KubernetesConfig{
        AllowedPullPolicies: []common.DockerPullPolicy{common.PullPolicyAlways, common.PullPolicyNever},
    }
    e.options = &kubernetesOptions{
        Image: spec.Image{PullPolicies: []common.DockerPullPolicy{common.PullPolicyAlways}},
        Services: map[string]*spec.Image{
            // Attacker-crafted service whose alias collides with the reserved helper container name.
            helperContainerName: {
                Name:         "attacker/image",
                PullPolicies: []spec.PullPolicy{common.PullPolicyNever},
            },
        },
    }

    pm, err := e.preparePullManager()
    require.NoError(t, err)

    policy, err := pm.GetPullPolicyFor(helperContainerName)
    require.NoError(t, err)
    // Expect the helper container to keep the build-image-derived policy (Always),
    // not the attacker service's policy (Never).
    assert.Equal(t, api.PullAlways, policy,
        "helper container pull policy must not be overridden by a colliding service alias")
}
```
Expected current (buggy) behavior: `policy == api.PullNever`, proving the collision lets the attacker-controlled service overwrite the reserved container's pull policy.

### Citations

**File:** executors/kubernetes/kubernetes.go (L489-506)
```go
	dockerPullPoliciesPerContainer := map[string][]common.DockerPullPolicy{
		buildContainerName:          s.options.Image.PullPolicies,
		helperContainerName:         s.options.Image.PullPolicies,
		initPermissionContainerName: s.options.Image.PullPolicies,
	}
	// Concrete's container set differs from legacy: it has an
	// init-steps-bootstrap init container (running the helper image) and no
	// helper container. The pull manager keys its retry cursor per container
	// name, so the bootstrap container must be registered here for pull-retry
	// to cover helper-image pull failures. This is neutral pull-policy
	// plumbing, not script-execution logic, so the conditional is retained
	// deliberately rather than forked into a separate builder.
	if s.Build.UseNativeSteps() {
		dockerPullPoliciesPerContainer[stepsBootstrapInitContainerName] = s.options.Image.PullPolicies
	}
	for containerName, service := range s.options.Services {
		dockerPullPoliciesPerContainer[containerName] = service.PullPolicies
	}
```

**File:** executors/kubernetes/kubernetes.go (L508-532)
```go
	k8sPullPoliciesPerContainer := map[string][]api.PullPolicy{}
	for containerName, pullPolicies := range dockerPullPoliciesPerContainer {
		k8sPullPolicies, err := s.getPullPolicies(pullPolicies)
		if err != nil {
			return nil, &common.BuildError{
				Inner:         fmt.Errorf("converting pull policy for container %q: %w", containerName, err),
				FailureReason: common.ConfigurationError,
			}
		}

		k8sPullPolicies, err = pull_policies.ComputeEffectivePullPolicies(
			k8sPullPolicies, allowedPullPolicies, pullPolicies, s.Config.Kubernetes.PullPolicy)
		if err != nil {
			return nil, &common.BuildError{
				Inner:         fmt.Errorf("invalid pull policy for container %q: %w", containerName, err),
				FailureReason: common.ConfigurationError,
			}
		}

		s.BuildLogger.Println(fmt.Sprintf("Using effective pull policy of %s for container %s", k8sPullPolicies, containerName))

		k8sPullPoliciesPerContainer[containerName] = k8sPullPolicies
	}

	return pull.NewPullManager(k8sPullPoliciesPerContainer, &s.BuildLogger), nil
```

**File:** executors/kubernetes/kubernetes.go (L3414-3429)
```go
func getServiceName(svc *spec.Image, usedAliases map[string]struct{}) string {
	for _, alias := range svc.Aliases() {
		if _, ok := usedAliases[alias]; ok {
			continue
		}
		if len(validation.IsDNS1123Label(alias)) != 0 {
			usedAliases[alias] = struct{}{}
			continue
		}

		usedAliases[alias] = struct{}{}
		return alias
	}

	return ""
}
```

**File:** helpers/pull_policies/pull_policies.go (L12-34)
```go
func ComputeEffectivePullPolicies[A ~string, B ~string, C ~[]string](pullPolicies, allowedPullPolicies []A, ciPullPolicies []B, executorPullPoilcies C) ([]A, error) {
	if len(pullPolicies) == 0 {
		return allowedPullPolicies, nil
	}
	if len(allowedPullPolicies) == 0 {
		return pullPolicies, nil
	}
	var actuallyAllowed []A
	for _, policy := range pullPolicies {
		if slices.Contains(allowedPullPolicies, policy) {
			actuallyAllowed = append(actuallyAllowed, policy)
		}
	}

	if len(actuallyAllowed) == 0 {
		return nil, &incompatiblePullPolicyError[A]{
			pullPolicies:        pullPolicies,
			allowedPullPolicies: allowedPullPolicies,
			pullPolicySource:    getPullPolicySource(ciPullPolicies, executorPullPoilcies),
		}
	}
	return actuallyAllowed, nil
}
```
