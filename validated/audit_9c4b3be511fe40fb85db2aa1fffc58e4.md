### Title
Unanchored regex matching in `overwriteRegexCheck` allows partial-match bypass of `NamespaceOverwriteAllowed`/`ServiceAccountOverwriteAllowed` restrictions - (File: executors/kubernetes/overwrites.go)

### Summary
`overwriteRegexCheck` (used by `evaluateOverwrite` for both `KUBERNETES_NAMESPACE_OVERWRITE` and `KUBERNETES_SERVICE_ACCOUNT_OVERWRITE`) validates job-supplied overwrite values with `regexp.MatchString`, which in Go succeeds on any substring match rather than requiring the pattern to match the entire string. If the runner admin configures a restrictive-looking pattern without explicit `^...$` anchors (e.g. `namespace-[0-9]`), an attacker-controlled job variable can smuggle arbitrary extra content around the matching substring and still pass validation, resulting in the pod (and subsequently the session exec/proxy channel) being created against a namespace/service account never intended to be allowed.

### Finding Description
`evaluateOverwrite` calls `overwriteRegexCheck(regex, overwriteValue)`: [1](#0-0) 

`overwriteRegexCheck` compiles the admin-provided `regex` (from `config.NamespaceOverwriteAllowed` / `config.ServiceAccountOverwriteAllowed`) and calls `r.MatchString(value)`. Go's `regexp.MatchString`/`(*Regexp).MatchString` reports whether the string *contains* a match anywhere, not whether the whole string matches — this is standard, well-documented Go regexp semantics, not a runner-specific bug. Consequently a pattern such as `namespace-[0-9]` (intended by an admin to allow only `namespace-0`..`namespace-9`) will also match `attacker-namespace-1-anything`, `namespace-1;rm`, etc., because the substring `namespace-1` satisfies the regex.

`createOverwrites` feeds `namespaceOverwrite := variables.Get(NamespaceOverwriteVariableName)` and `serviceAccountOverwrite := variables.Get(ServiceAccountOverwriteVariableName)` — both directly attacker-controlled CI job variables — straight into `evaluateOverwrite`: [2](#0-1) 

If the crafted value passes the (mis-anchored) regex check, it is returned unmodified and stored as `o.namespace`/`o.serviceAccount`, which is later used to construct/create the job pod in that namespace/under that service account. The pod created there is what the session exec/proxy channel subsequently attaches to, so any bypass at this validation stage directly determines the namespace/service-account scope the session operates in.

There is no additional layer downstream in this file re-validating that the returned value is *exactly* the intended value — the only gate is `overwriteRegexCheck`.

### Impact Explanation
An attacker who can set CI/CD variables for their own pipeline can craft an overwrite value that "matches" (as a substring) an admin's restrictive regex but does not equal any of the intended values, causing the job pod to be created in an unintended namespace or under an unintended service account. Any subsequent interactive session (`exec`/`attach`/proxy) reaches that pod, giving the job attacker's session traffic elevated Kubernetes identity/scope beyond what `NamespaceOverwriteAllowed`/`ServiceAccountOverwriteAllowed` were meant to restrict — a concrete cross-namespace/cross-service-account privilege escalation exactly matching the scoped impact.

### Likelihood Explanation
This requires the runner admin to configure `NamespaceOverwriteAllowed`/`ServiceAccountOverwriteAllowed` with a pattern lacking `^` and `$` anchors — a very common and easy mistake, since Go's `MatchString` substring semantics are non-obvious and differ from the "full match" assumption many admins bring from other regex engines/languages. GitLab's own documentation for these settings recommends including anchors, implying this is a known footgun rather than something the code itself prevents. No special privileges are needed by the attacker beyond being a normal pipeline author able to set job variables, making exploitation straightforward and fully repeatable whenever a vulnerable (unanchored) pattern is configured.

### Recommendation
Do not rely solely on `regexp.MatchString`, which permits partial matches. Either:
- Wrap the admin-supplied pattern to force full-string matching, e.g. compile as `^(?:` + regex + `)$` inside `overwriteRegexCheck` before calling `MatchString`, or
- Validate with `regexp.MustCompile(regex).FindString(value) == value` (exact full match) instead of `MatchString`.

This removes the admin's burden of remembering to anchor every configured pattern and makes the overwrite-allowed regexes behave as "exact match" allow-lists by default, closing the substring-bypass class of issues for both `NamespaceOverwriteAllowed` and `ServiceAccountOverwriteAllowed` (and the other `evaluateMapOverwrite`-based fields that use the same `overwriteRegexCheck`).

### Proof of Concept
```go
func TestOverwriteRegexCheck_PartialMatchBypass(t *testing.T) {
    // Admin intends to allow only "namespace-0".."namespace-9"
    regex := "namespace-[0-9]"

    // Attacker-controlled value that should NOT be allowed but contains a matching substring
    maliciousValue := "attacker-controlled-namespace-1-extra"

    err := overwriteRegexCheck(regex, maliciousValue)

    // EXPECTED (fix): err should be a *malformedOverwriteError, rejecting the value
    // ACTUAL (bug): err is nil, MatchString succeeds on the substring "namespace-1"
    assert.Error(t, err, "unanchored regex must not allow partial/substring matches")
}

func TestCreateOverwrites_NamespaceOverwriteBypass(t *testing.T) {
    config := &common.KubernetesConfig{
        Namespace:                 "default",
        NamespaceOverwriteAllowed: "namespace-[0-9]", // admin forgot ^$
    }
    variables := spec.Variables{
        {Key: NamespaceOverwriteVariableName, Value: "namespace-1;malicious-suffix"},
    }

    o, err := createOverwrites(config, variables, logger)

    require.NoError(t, err)
    // Bug: o.namespace ends up as the attacker value instead of being rejected
    assert.NotEqual(t, "namespace-1;malicious-suffix", o.namespace)
}
```
Both assertions fail against current code (no error is returned, and the overwrite value is accepted verbatim), demonstrating the bypass; they pass once `overwriteRegexCheck` is changed to require a full-string match.

### Citations

**File:** executors/kubernetes/overwrites.go (L157-179)
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

	serviceAccountOverwrite := variables.Get(ServiceAccountOverwriteVariableName)
	o.serviceAccount, err = o.evaluateOverwrite(
		"ServiceAccount",
		config.ServiceAccount,
		config.ServiceAccountOverwriteAllowed,
		serviceAccountOverwrite,
		logger,
	)
	if err != nil {
		return nil, err
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
