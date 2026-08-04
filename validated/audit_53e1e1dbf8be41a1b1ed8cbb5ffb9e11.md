### Title
Unanchored regex matching in `overwriteRegexCheck` allows substring bypass of `*_OverwriteAllowed` restrictions - ([File: executors/kubernetes/overwrites.go])

### Summary
`overwriteRegexCheck` validates user-supplied Kubernetes overwrite values (namespace, service account, pod labels, etc.) using `regexp.MatchString`, which performs an unanchored substring search rather than a full-string match. If an admin configures an `*_OverwriteAllowed` pattern without explicit `^`/`$` anchors (e.g. `ci-.*` instead of `^ci-.*$`), an unprivileged job author can supply a crafted value that merely contains a matching substring and have it accepted as a valid overwrite.

### Finding Description
`createOverwrites` reads job variables such as `KUBERNETES_NAMESPACE_OVERWRITE` and `KUBERNETES_SERVICE_ACCOUNT_OVERWRITE` and passes them through `evaluateOverwrite`, which calls `overwriteRegexCheck(regex, overwriteValue)` [1](#0-0) . That helper compiles the admin-configured regex and calls `r.MatchString(value)`: [2](#0-1) 

`regexp.MatchString` in Go reports whether the pattern matches *anywhere* in the string, not whether the whole string matches — it is not implicitly anchored. So a pattern such as `ci-.*` (missing `^...$`) will match `xci-foo`, `ci-foo\nadmin-ns`, or any string that contains a substring satisfying the pattern. The same unanchored `overwriteRegexCheck` call is reused for pod labels/annotations/node selectors/node tolerations via `evaluateMapOverwrite` [3](#0-2) , so the same class of bypass applies there too.

This is a genuine gap in the Runner's own validation logic (not merely a documented admin misconfiguration choice): the function name `evaluateOverwrite`/`overwriteRegexCheck` and its error type `malformedOverwriteError` ("provided value %q does not match %q") strongly imply the intended semantic is "the value must match the pattern," i.e. a full match, but the implementation only guarantees a substring match. There is no additional check anywhere in the call chain (`createOverwrites` → `evaluateOverwrite` → `overwriteRegexCheck`) that anchors or fully matches the value against the regex.

### Impact Explanation
If an admin's `NamespaceOverwriteAllowed`/`ServiceAccountOverwriteAllowed` regex is not explicitly anchored with `^...$` (a very natural and common regex authoring mistake, and one that Runner's own validation function does nothing to prevent or warn about), a pipeline author can inject `KUBERNETES_NAMESPACE_OVERWRITE` or `KUBERNETES_SERVICE_ACCOUNT_OVERWRITE` values that are outside the intended allow-set but still satisfy `MatchString`. The job pod is then created in an unintended namespace or run under an unintended service account, potentially granting it a stronger/broader cluster identity or RBAC permissions than the admin intended to allow for that project's jobs.

### Likelihood Explanation
Exploitability depends entirely on the specific regex an admin configures. If the admin's regex is already anchored (e.g. `^ci-.*$`), this bug has no effect — Go's `MatchString` with an anchored pattern behaves as a full match. The bug only manifests when the admin writes an unanchored regex, which is a realistic and common configuration error given regex authoring habits (many admins assume `MatchString`/regex-based "allow" checks are implicitly full-string matches). Since Runner performs no anchoring or full-match enforcement itself, and provides no warning that patterns must be anchored, the security guarantee of `*_OverwriteAllowed` is fragile and can silently fail for a plausible subset of admin configurations. The GitLab Runner Kubernetes executor is widely used with these overwrite variables, so the precondition (restrictive but unanchored regex) is not far-fetched.

### Recommendation
Change `overwriteRegexCheck` (and by extension `evaluateOverwrite`/`evaluateMapOverwrite`) to require a full-string match instead of relying on `regexp.MatchString`'s substring semantics — e.g., wrap the compiled pattern check with `^(?:` + regex + `)$` before matching, or use `regexp.MustCompile("^(?:" + regex + ")$").MatchString(value)`, or explicitly reject values containing unexpected structural characters (like `\n`). Document this behavior clearly and consider validating at config-load time that `*_OverwriteAllowed` patterns are anchored, emitting a warning otherwise.

### Proof of Concept
```go
func TestOverwriteRegexCheck_UnanchoredBypass(t *testing.T) {
    // Admin intends to only allow namespaces literally matching "ci-<something>"
    regex := "ci-.*"

    // Should be rejected but currently passes due to unanchored MatchString
    err := overwriteRegexCheck(regex, "xci-foo")
    assert.Error(t, err, "value with extra prefix should not satisfy an unanchored 'allowed' pattern")

    // Newline-injected value should also be rejected
    err = overwriteRegexCheck(regex, "ci-foo\nadmin-ns")
    assert.Error(t, err, "value containing a matching substring plus extra content should be rejected")
}
```
Both assertions currently fail (i.e., `overwriteRegexCheck` returns `nil` error) because `regexp.MatchString` finds `ci-foo` as a matching substring within the larger string, confirming the bypass at `executors/kubernetes/overwrites.go:626` [2](#0-1) . A full end-to-end reproduction would call `createOverwrites` with a `KubernetesConfig{NamespaceOverwriteAllowed: "ci-.*"}` and a `spec.Variables` set containing `KUBERNETES_NAMESPACE_OVERWRITE=xci-malicious-ns`, asserting that the resulting `overwrites.namespace` is not `"xci-malicious-ns"` (i.e., that it is rejected with a `malformedOverwriteError`).

### Citations

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

**File:** executors/kubernetes/overwrites.go (L659-696)
```go
func (o *overwrites) evaluateMapOverwrite(
	fieldName string,
	values map[string]string,
	regex string,
	variables spec.Variables,
	variablesSelector string,
	logger buildlogger.Logger,
	split func(string) (string, string, error),
) (map[string]string, error) {
	if regex == "" {
		logger.Debugln("Regex allowing overrides for", fieldName, "is empty, disabling override.")
		return values, nil
	}

	finalValues := make(map[string]string)
	for k, v := range values {
		finalValues[k] = v
	}

	for _, variable := range variables {
		if !strings.HasPrefix(variable.Key, variablesSelector) {
			continue
		}

		if err := overwriteRegexCheck(regex, variable.Value); err != nil {
			return nil, err
		}

		key, value, err := split(variable.Value)
		if err != nil {
			return nil, err
		}

		finalValues[key] = value
		logger.Println(fmt.Sprintf("%q %q overwritten with %q", fieldName, key, value))
	}
	return finalValues, nil
}
```
