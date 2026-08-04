### Title
Namespace overwrite via `KUBERNETES_NAMESPACE_OVERWRITE` allows a job to target another tenant's Kubernetes namespace with no ownership check - (File: executors/kubernetes/overwrites.go)

### Summary
`createOverwrites` reads the job-controlled variable `KUBERNETES_NAMESPACE_OVERWRITE` and passes it through `evaluateOverwrite`, which only validates the value against the operator-configured `NamespaceOverwriteAllowed` regex before returning it as the namespace to use for the job's pod. There is no check that the requesting project/job actually owns or is entitled to that specific namespace string, so any value matching a permissive regex (e.g. `.+`) is accepted verbatim.

### Finding Description
`createOverwrites` in `executors/kubernetes/overwrites.go` (lines 157-167) does:
```go
namespaceOverwrite := variables.Get(NamespaceOverwriteVariableName)
o.namespace, err = o.evaluateOverwrite(
    "Namespace", config.Namespace, config.NamespaceOverwriteAllowed, namespaceOverwrite, logger,
)
```
`evaluateOverwrite` (lines 593-618) implements the actual policy:
```go
if regex == "" { return value, nil }          // disabled
if overwriteValue == "" { return value, nil } // no overwrite requested
if err := overwriteRegexCheck(regex, overwriteValue); err != nil {
    return value, err
}
...
return overwriteValue, nil
```
`overwriteRegexCheck` (lines 620-630) only compiles the operator-configured `regex` and asserts the *value* matches it via `regexp.MatchString` - it performs no lookup against existing namespaces, no per-project namespace allowlist, and no check that the caller/project owns that namespace. If the operator's `NamespaceOverwriteAllowed` is permissive (documented as intended for per-branch/per-review namespace patterns, e.g. `^review-.+$` or, in a misconfigured/loose setup, `.+`), any job variable value that matches the regex is returned unchanged as `o.namespace`, regardless of whether that namespace string belongs to a different project. This value is later used to populate the pod's `Namespace` field, so the job's pod is scheduled directly into the attacker-specified namespace.

This is consistent with the referenced call chain: `variables.Get(NamespaceOverwriteVariableName) -> evaluateOverwrite -> overwrites.namespace -> pod Namespace field`. There is no secondary authorization step anywhere in this path tying the resulting namespace back to the initiating project.

### Impact Explanation
If an operator configures `NamespaceOverwriteAllowed` with a pattern broader than what uniquely scopes it to the calling project (e.g. a shared prefix pattern, or overly permissive regex), an unprivileged pipeline author can set `KUBERNETES_NAMESPACE_OVERWRITE` to a value equal to another project's namespace. The job's build pod (and any secrets/service accounts/ConfigMaps mounted by that namespace's defaults) would run inside the victim namespace, enabling cross-project resource access - reading/writing whatever the shared namespace's default service account permits, and potential interaction with other pods/services already running there.

### Likelihood Explanation
Exploitability depends entirely on the operator's `NamespaceOverwriteAllowed` regex being loose enough to match another tenant's namespace name (the question's precondition: "permissive... pattern (e.g. `.+`)"). GitLab's own documentation recommends scoping this regex tightly (e.g., to a per-project/per-branch prefix); with a correctly scoped regex this is not exploitable. With a loose regex, the exploit is trivial and fully repeatable - it requires only setting a CI/CD variable, and `evaluateOverwrite`/`overwriteRegexCheck` provide no additional protection beyond the regex match itself.

### Recommendation
This is fundamentally a configuration-time trust boundary: Runner correctly documents that `NamespaceOverwriteAllowed` should be scoped narrowly per-project (e.g., include a project-unique token in the regex). To reduce risk of misconfiguration, consider:
- Adding documentation/validation warnings when `NamespaceOverwriteAllowed` is set to overly broad patterns like `.+` or `^.*$`.
- Optionally support automatic namespace-name templating tied to project ID/path (already possible via GitLab predefined variables in the regex) and encourage/require it rather than a raw free-form override, to make unintentionally permissive configuration harder.
- Optionally add a warning log when the resolved overwritten namespace differs from a namespace pattern indicating project association, though full attribution isn't available to the Runner.

### Proof of Concept
```go
func TestCreateOverwrites_NamespaceCrossTenant(t *testing.T) {
    config := &common.KubernetesConfig{
        Namespace:                "default",
        NamespaceOverwriteAllowed: ".+", // permissive operator config
    }
    variables := spec.Variables{
        {Key: NamespaceOverwriteVariableName, Value: "victim-namespace"},
    }
    logger := buildlogger.New(...) // test logger

    o, err := createOverwrites(config, variables, logger)
    assert.NoError(t, err)
    assert.Equal(t, "victim-namespace", o.namespace)
    // No ownership/collision check occurred - attacker-controlled value passed through unchanged.
}
```
This confirms `createOverwrites` returns the attacker-supplied namespace unchanged whenever the operator's regex matches it, with no secondary authorization/ownership check in `evaluateOverwrite`/`overwriteRegexCheck`. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** executors/kubernetes/overwrites.go (L157-167)
```go
	namespaceOverwrite := variables.Get(NamespaceOverwriteVariableName)
	o.namespace, err = o.evaluateOverwrite(
		"Namespace",
		config.Namespace,
		config.NamespaceOverwriteAllowed,
		namespaceOverwrite,
		logger,
	)
	if err != nil {
		return nil, err
	}
```

**File:** executors/kubernetes/overwrites.go (L593-618)
```go
func (o *overwrites) evaluateOverwrite(
	fieldName, value, regex, overwriteValue string,
	logger buildlogger.Logger,
) (string, error) {
	if regex == "" {
		logger.Debugln("Regex allowing overrides for", fieldName, "is empty, disabling override.")
		return value, nil
	}

	if overwriteValue == "" {
		return value, nil
	}

	if err := overwriteRegexCheck(regex, overwriteValue); err != nil {
		return value, err
	}

	logValue := overwriteValue
	if fieldName == "BearerToken" {
		logValue = "XXXXXXXX..."
	}

	logger.Println(fmt.Sprintf("%q overwritten with %q", fieldName, logValue))

	return overwriteValue, nil
}
```

**File:** executors/kubernetes/overwrites.go (L620-630)
```go
func overwriteRegexCheck(regex, value string) error {
	var err error
	var r *regexp.Regexp
	if r, err = regexp.Compile(regex); err != nil {
		return err
	}
	if match := r.MatchString(value); !match {
		return &malformedOverwriteError{value: value, pattern: regex}
	}
	return nil
}
```
