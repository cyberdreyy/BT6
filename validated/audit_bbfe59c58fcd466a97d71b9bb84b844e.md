Based on the investigation, I was not able to fully trace the `getContainerUidGid`-style function in `executors/kubernetes/kubernetes.go` to its completion before running out of tool calls, but the test file evidence (`executors/kubernetes/kubernetes_test.go`) strongly indicates a real analog exists.

### Title
Security context `RunAsUser`/`RunAsGroup` bypasses the `allowed_users`/`allowed_groups` restriction - (`executors/kubernetes/kubernetes.go`)

### Summary
The external report's root cause pattern is: an authorization/restriction check (`earned()`) is computed from the wrong piece of state (veRAAC balance) instead of the state the restriction is actually meant to gate (staked balance), so the check can be satisfied without meeting the real precondition. The analogous pattern in GitLab Runner is that the Kubernetes executor's `allowed_users` / `allowed_groups` restriction — enforced via `KubernetesConfig.IsUserAllowed` / `IsGroupAllowed` (`common/config.go:235-241`, delegating to `isUserOrGroupAllowed` at `common/config.go:204-233`) — is validated against the job's `image:user` string, but the actual runtime identity used to launch the container can come from a separate source: the pod/container `securityContext.RunAsUser` / `RunAsGroup`. Test cases in `executors/kubernetes/kubernetes_test.go:8786-9003` ("security context bypasses user allowlist completely", "security context bypasses group allowlist completely", "pod security context bypasses job user allowlist validation", "container security context bypasses validation while pod provides fallback") confirm that when a security context UID/GID is set, the resulting effective UID/GID is taken from the security context and the `allowedUsers`/`allowedGroups` check is skipped entirely — even producing UID 0 (root) despite root being explicitly excluded from the allowlist. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The intended security control is: a runner admin sets `allowed_users` / `allowed_groups` in the Kubernetes executor config to restrict which UID/GID a job's container image may run as, preventing arbitrary or root execution. This is enforced through `KubernetesConfig.IsUserAllowed`/`IsGroupAllowed`, which reject an ID not present in the allowlist and specifically require root (`0`) to be explicitly allowed (`common/config.go:215-222`). However, the test suite shows that whenever a `RunAsUser`/`RunAsGroup` value is present in either the container-level or pod-level `securityContext` (fields configurable via `.gitlab-ci.yml` job-level Kubernetes pod overrides, depending on runner permission settings), the resulting UID/GID is taken directly from the security context and the allowlist check is bypassed — including for UID `0` (root), which is supposed to require explicit allow-listing.

This mirrors the `BaseGauge` bug exactly: a restriction meant to gate on one piece of state (`staked balance` / `allowed_users`) is instead satisfiable via a different, attacker-influenceable piece of state (`veRAAC balance` / `securityContext.RunAsUser`) that the check doesn't consult.

### Impact Explanation
If a runner is configured with `allowed_users`/`allowed_groups` specifically to prevent jobs from running containers as root or as an unapproved UID (a documented security hardening mechanism), a job pipeline author who can influence the pod/container security context (e.g., via `.gitlab-ci.yml` `kubernetes.pod_spec` overrides, if permitted by the runner's `allow_pod_spec_overrides` / similar features) could bypass this restriction entirely and run as root or any arbitrary UID, defeating the intended control.

### Likelihood Explanation
Likelihood depends heavily on whether pod/container security-context overrides are attacker-controllable in a given deployment (i.e., whether the runner allows job-level pod-spec overrides). I was unable to fully confirm, within the remaining tool budget, whether the security-context values consumed in `getContainerUidGid`-style logic in `executors/kubernetes/kubernetes.go` originate from admin-only runner config or from job-controllable `.gitlab-ci.yml` overrides — this is the critical fact needed to determine if this crosses the "attacker-controlled entry path" bar versus being an admin-trusted configuration interaction. I could not verify the exact call site (only test file evidence, not the production function body, was retrieved before iterations ran out).

### Recommendation
If `allowed_users`/`allowed_groups` is configured, the effective UID/GID actually used to run the container — whether derived from image default, job `user:` field, or pod/container security context — should be validated against the allowlist before pod creation, not only the job-supplied `user` string.

### Proof of Concept
Cannot be fully constructed without the production source of the UID/GID resolution function (`executors/kubernetes/kubernetes.go`) — only the associated test file (`kubernetes_test.go:8785-9010`) was retrieved. **This finding is incomplete/unverified**: I could not confirm (a) the exact production code path that resolves effective UID/GID and skips the allowlist check, or (b) whether the security-context values are job-controllable versus admin-only. A full confirmation would require reading the `getContainerUidGid`/equivalent function body directly, which was not retrieved before the tool budget was exhausted.

### Citations

**File:** executors/kubernetes/kubernetes_test.go (L8785-8804)
```go
		{
			name:    "security context bypasses user allowlist completely",
			jobUser: "1000:1001",
			securityContext: common.KubernetesContainerSecurityContext{
				RunAsUser: common.Int64Ptr(9999),
			},
			allowedUsers: []string{"1000", "2000"},
			expectedUID:  9999,
			expectedGID:  1001,
		},
		{
			name:    "security context bypasses group allowlist completely",
			jobUser: "1000:1001",
			securityContext: common.KubernetesContainerSecurityContext{
				RunAsGroup: common.Int64Ptr(9999),
			},
			allowedGroups: []string{"1001", "2001"},
			expectedUID:   1000,
			expectedGID:   9999,
		},
```

**File:** executors/kubernetes/kubernetes_test.go (L8990-9003)
```go
		{
			name:    "container security context bypasses validation while pod provides fallback",
			jobUser: "",
			containerSecurityContext: common.KubernetesContainerSecurityContext{
				RunAsUser: common.Int64Ptr(0), // root - normally blocked
			},
			podSecurityContext: common.KubernetesPodSecurityContext{
				RunAsUser:  common.Int64Ptr(2000),
				RunAsGroup: common.Int64Ptr(2001),
			},
			allowedUsers: []string{"1000", "65534"}, // root not allowed
			expectedUID:  0,                         // container overrides and bypasses validation
			expectedGID:  2001,                      // pod provides group
		},
```

**File:** common/config.go (L204-241)
```go
func (c KubernetesConfig) isUserOrGroupAllowed(idStr string, kind allowListKind, allowedList []string) error {
	// default image user is allowed.
	if idStr == "" {
		return nil
	}

	id, err := parseID(idStr)
	if err != nil {
		return fmt.Errorf("%s %q is invalid: %w", kind, idStr, err)
	}

	// Root requires explicit permission in allowlist, even if allowlist is empty.
	// Compare numerically so that "00", "000", etc. are all treated as UID/GID 0.
	if id == 0 {
		if allowListContainsID(0, kind, allowedList) {
			return nil
		}
		return fmt.Errorf("%s %q is not in the allowed list: %v", kind, idStr, allowedList)
	}

	// if no allowed-users/groups have been specified in the runner config, any non-root user is allowed.
	if len(allowedList) == 0 {
		return nil
	}

	if allowListContainsID(id, kind, allowedList) {
		return nil
	}
	return fmt.Errorf("%s %q is not in the allowed list: %v", kind, idStr, allowedList)
}

func (c KubernetesConfig) IsUserAllowed(user string) error {
	return c.isUserOrGroupAllowed(user, allowListKindUser, c.AllowedUsers)
}

func (c KubernetesConfig) IsGroupAllowed(group string) error {
	return c.isUserOrGroupAllowed(group, allowListKindGroup, c.AllowedGroups)
}
```
