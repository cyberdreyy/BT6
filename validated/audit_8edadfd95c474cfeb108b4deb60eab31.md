## Analysis Summary

I mapped the reported vulnerability class ("a restriction mechanism can be bypassed via an alternate code path that was not updated with the same enforcement logic") onto the GitLab Runner Kubernetes executor's UID/GID allow-listing logic. The analog is strong and is directly demonstrated by the repository's own test suite.

### Title
Container/Pod SecurityContext RunAsUser/RunAsGroup completely bypasses `AllowedUsers`/`AllowedGroups` restriction - (File: `executors/kubernetes/kubernetes.go`, function `getContainerUIDGID`)

### Summary
GitLab Runner's Kubernetes executor exposes `AllowedUsers` / `AllowedGroups` runner configuration settings intended to block jobs from running containers as disallowed users (in particular, `root`/UID 0). However, when a `KubernetesContainerSecurityContext` or `KubernetesPodSecurityContext` (`RunAsUser`/`RunAsGroup`) is supplied, `getContainerUIDGID` applies it unconditionally and skips the allow-list validation path entirely — mirroring the reported issue where a "special" code path (the neutral adapter) bypassed the ratio-based blocking mechanism that the "normal" path enforced.

### Finding Description
The test suite in [1](#0-0)  explicitly documents two cases named "security context bypasses user allowlist completely" and "security context bypasses group allowlist completely": when `AllowedUsers`/`AllowedGroups` is set to disallow certain UIDs/GIDs (e.g. only `1000`/`2000` allowed), supplying a `RunAsUser`/`RunAsGroup` value outside that list (e.g. `9999`) in the container security context is accepted verbatim, with no validation error and no warning.

The pattern repeats and is amplified with `KubernetesPodSecurityContext` at [2](#0-1) , where the test names are literally "pod security context bypasses job user allowlist validation" and "container security context bypasses validation while pod provides fallback" — even a `RunAsUser: 0` (root), explicitly excluded from `allowedUsers`, is accepted and used (`expectedUID: 0 // container overrides and bypasses validation`).

By contrast, when only the job-supplied `image:`/`jobUser` field is used (without a security context), the same function correctly rejects disallowed values, e.g. `"root user blocked by allowlist"` at [3](#0-2)  and `"root group blocked by default (no allowlist)"` at [4](#0-3) . This confirms that `AllowedUsers`/`AllowedGroups` enforcement exists on one code path (job-user derived UID/GID) but is entirely absent on the security-context-derived path — exactly the same "ratio=0 vs. neutral adapter" asymmetry described in the external report: a control that visibly exists is silently inapplicable through an alternate, still-reachable route.

The relevant restriction/validation and its bypass live inside `getContainerUIDGID`, referenced by all tests as `executor.getContainerUIDGID(tt.jobUser, "build", tt.securityContext)` at [5](#0-4) , and the config fields themselves are wired at [6](#0-5) .

### Impact Explanation
`AllowedUsers`/`AllowedGroups` is a security control specifically meant to prevent CI jobs (potentially running attacker-supplied Docker images, e.g., in fork/MR pipelines) from executing as `root` inside Kubernetes pods on a shared cluster. If a security-context value (whether set directly in runner config or, in deployments that allow per-job overwrite via CI/CD variables such as `KUBERNETES_CONTAINER_SECURITY_CONTEXT_RUN_AS_USER_OVERWRITE`/`KUBERNETES_POD_SECURITY_CONTEXT_RUN_AS_USER_OVERWRITE`) can silently bypass the allow-list, a job can run as `root` (UID 0) or any other explicitly disallowed UID/GID inside the build container despite the administrator's explicit restriction. On a multi-tenant Kubernetes cluster this defeats the intended defense-in-depth against container breakout / privilege abuse that `AllowedUsers` exists to provide.

### Likelihood Explanation
I was **not able to fully verify** the exact source of the `KubernetesContainerSecurityContext`/`KubernetesPodSecurityContext` values reaching `getContainerUIDGID` at runtime (i.e., whether these values are exclusively admin-set in `config.toml`, or whether they can also originate from job-supplied CI/CD variable overwrites gated by an admin-enabled overwrite-allowed regex). This distinction is critical:
- If these values are **only** settable by the runner administrator in `config.toml`, this is an admin-controlled setting and the finding would likely be disqualified as "trusted-role compromise required."
- If GitLab Runner's documented `*_overwrite_allowed` variable-overwrite feature allows a CI/CD pipeline (job author) to set these values even when an administrator has configured `AllowedUsers`/`AllowedGroups` to restrict them, then the bypass is reachable by an attacker who can influence pipeline variables (e.g., in an MR from an external contributor), which is a realistic and previously-recognized threat model for GitLab Runner.

I could not read the full body of `getContainerUIDGID` in `executors/kubernetes/kubernetes.go` in this session to confirm which of these applies, only the unit tests describing its externally observable behavior. This should be confirmed by inspecting `executors/kubernetes/kubernetes.go` directly (the function itself and its callers) and the config-overwrite validation logic (search for `*_overwrite_allowed` and `ValidateOverwrite` in the kubernetes executor package) before treating this as a confirmed, in-scope, reportable vulnerability.

### Recommendation
If security-context overwrite values can originate from job-controlled variables, `getContainerUIDGID` should apply the same `AllowedUsers`/`AllowedGroups` allow-list check to the UID/GID resolved from `KubernetesContainerSecurityContext`/`KubernetesPodSecurityContext` as it does to the job-derived UID/GID, rejecting (or ignoring with a warning, per existing "not allowed" warning pattern in [7](#0-6) ) any security-context override that falls outside the configured allow-list.

### Proof of Concept
Based on the test cases (executable as-is against the current codebase):
1. Configure `Kubernetes.AllowedUsers = []string{"1000", "2000"}`.
2. Submit a job with `jobUser = "1000:1001"` and a `KubernetesContainerSecurityContext{RunAsUser: Int64Ptr(9999)}`.
3. Per [8](#0-7) , `getContainerUIDGID` returns UID `9999` — a value never in the allow-list — with no warning or error, demonstrating the bypass.
4. Similarly, with `Kubernetes.AllowedUsers = []string{"1000", "2000"}` and a `KubernetesPodSecurityContext{RunAsUser: Int64Ptr(9999)}`, per [9](#0-8) , UID `9999` is again accepted unchecked.

**Caveat:** this PoC demonstrates the internal function behavior confirmed by existing unit tests; a full end-to-end PoC against a live runner requires confirming (in `executors/kubernetes/kubernetes.go` and its config-overwrite handling) whether an unprivileged CI job author can actually supply these security-context values at job submission time, which I was unable to verify within this session.

### Citations

**File:** executors/kubernetes/kubernetes_test.go (L8746-8752)
```go
			name:          "root user blocked by allowlist",
			jobUser:       "0:0",
			allowedUsers:  []string{"1000", "65534"}, // Root (0) not in list
			expectedUID:   -1,                        // Validation failure returns -1
			expectedGID:   -1,                        // Root group also blocked by default
			expectWarning: "user \"0\" is not in the allowed list:",
		},
```

**File:** executors/kubernetes/kubernetes_test.go (L8761-8768)
```go
		{
			name:    "root group blocked by default (no allowlist)",
			jobUser: "1000:0", // Non-root user, root group
			// No allowedGroups = root group blocked, non-root groups allowed
			expectedUID:   1000,
			expectedGID:   -1, // Validation failure returns -1
			expectWarning: "group \"0\" is not in the allowed list:",
		},
```

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

**File:** executors/kubernetes/kubernetes_test.go (L8842-8850)
```go
		{
			name:    "invalid job user format with container security context warns and continues",
			jobUser: "invalid:1000",
			securityContext: common.KubernetesContainerSecurityContext{
				RunAsUser: common.Int64Ptr(2000),
			},
			expectedUID:   2000,
			expectedGID:   1000,
			expectWarning: "Overriding user for container \"build\" to \"invalid\" is not allowed: user is set to 2000 in container security context",
```

**File:** executors/kubernetes/kubernetes_test.go (L8867-8868)
```go
			executor.Config.RunnerSettings.Kubernetes.AllowedUsers = tt.allowedUsers
			executor.Config.RunnerSettings.Kubernetes.AllowedGroups = tt.allowedGroups
```

**File:** executors/kubernetes/kubernetes_test.go (L8882-8882)
```go
			uid, gid := executor.getContainerUIDGID(tt.jobUser, "build", tt.securityContext)
```

**File:** executors/kubernetes/kubernetes_test.go (L8978-9003)
```go
		{
			name:    "pod security context bypasses job user allowlist validation",
			jobUser: "1000:1001",
			podSecurityContext: common.KubernetesPodSecurityContext{
				RunAsUser:  common.Int64Ptr(9999),
				RunAsGroup: common.Int64Ptr(9998),
			},
			allowedUsers:  []string{"1000", "2000"},
			allowedGroups: []string{"1001", "2001"},
			expectedUID:   9999,
			expectedGID:   9998,
		},
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
