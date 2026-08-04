### Title
Node selector/toleration overwrites can clear or override admin-configured baseline keys (e.g. `role=ci`) - ([File: executors/kubernetes/overwrites.go])

### Summary
`evaluateMapOverwrite` (used for both `NodeSelector` and `NodeTolerations` overwrites) seeds `finalValues` from the admin baseline map and then unconditionally overwrites any key supplied via a `KUBERNETES_NODE_SELECTOR_*`/`KUBERNETES_NODE_TOLERATIONS_*` job variable, with no check preventing a job from replacing a baseline key's value. If `node_selector_overwrite_allowed` (or the tolerations equivalent) is configured with a permissive value-matching regex, an unprivileged job can redefine `role` (or any other baseline selector/toleration key) to a different value than the admin's `role=ci`.

### Finding Description
In `executors/kubernetes/overwrites.go`, `createOverwrites` calls: [1](#0-0) 

which delegates to `evaluateMapOverwrite`: [2](#0-1) 

The function copies the baseline map (`config.NodeSelector`, e.g. `{"role":"ci"}`) into `finalValues`, then iterates all job variables with the `KUBERNETES_NODE_SELECTOR_` prefix. For each matching variable it only validates the **value** against the admin-configured regex (`overwriteRegexCheck(regex, variable.Value)`); it never checks whether the derived `key` (from `split(variable.Value)`, i.e. `splitMapOverwrite`) already exists in the baseline `finalValues` map, nor does it protect specific "reserved" baseline keys. The assignment `finalValues[key] = value` at line 692 blindly replaces any existing entry, including the admin's isolation-critical `role=ci` selector.

The regex gate (`config.NodeSelectorOverwriteAllowed`) is a value-pattern regex, not a key-allowlist — it's designed to constrain what values are acceptable (e.g. `^ci-.*$`), not to protect which selector keys can be touched. A job variable such as `KUBERNETES_NODE_SELECTOR_ROLE=role=production` (key derived via `splitMapOverwrite`, which splits on the first `=`) will pass as long as the whole string `role=production` matches the configured regex — which is very plausible for loosely configured regexes (e.g. `.*` or any pattern not anchored to specific key names). Once this passes, `finalValues["role"]` becomes `"production"`, silently discarding the runner's `role=ci` baseline entry that documentation states is meant to isolate CI pods from production/tainted nodes.

The same flaw applies to `NodeTolerationsOverwriteVariablePrefix`/`splitToleration`: a job-supplied toleration overwrite with the same key can replace an admin-defined toleration (or none exists at all, but the map-merge logic is symmetric — the same missing per-key protection applies).

### Impact Explanation
If an operator enables `node_selector_overwrite_allowed` with any regex that doesn't specifically restrict which selector *keys* can be set (the regex only constrains the string content of `KEY=VALUE`), an unprivileged job can overwrite the `role: ci` node selector to point pods at nodes outside the intended isolation boundary (e.g., production or tainted nodes), assuming those nodes are otherwise schedulable (no additional taints/tolerations required). This is a cross-tenant node isolation bypass: a job escapes the admin-intended `role=ci` node pool and can run on nodes it was never meant to reach, matching the scoped impact described.

### Likelihood Explanation
Requires `node_selector_overwrite_allowed` (or `node_tolerations_overwrite_allowed`) to be configured — an admin precondition, which is explicitly listed as an accepted precondition in the question. Given that precondition, exploitation is trivial and fully attacker-controlled: any job variable of the form `KUBERNETES_NODE_SELECTOR_<name>=role=production` reaching `createOverwrites` triggers the overwrite with no additional guard. This is deterministic and repeatable in every job run once the feature is enabled with a permissive regex — a realistic configuration since the regex is typically written to validate label *values*, not to defend key names from being clobbered.

### Recommendation
In `evaluateMapOverwrite`, reject (or require explicit separate configuration to permit) any job-supplied key that collides with a key already present in the admin baseline `values` map, unless the admin baseline value at that key is empty/absent. At minimum, document and enforce that reserved keys (such as those matching an admin-designated "protected keys" list, e.g. `role`) cannot be overwritten by job-controlled `KUBERNETES_NODE_SELECTOR_*`/`KUBERNETES_NODE_TOLERATIONS_*` variables regardless of the value-regex outcome. Consider changing `NodeSelectorOverwriteAllowed`/`NodeTolerationsOverwriteAllowed` semantics to be strictly additive by default (new keys only) with an explicit opt-in flag required to allow overwriting existing baseline keys.

### Proof of Concept
```go
func TestNodeSelectorOverwrite_CannotClearBaselineRoleKey(t *testing.T) {
    config := &common.KubernetesConfig{
        NodeSelector:              map[string]string{"role": "ci"},
        NodeSelectorOverwriteAllowed: ".*", // permissive value regex, plausible real-world config
    }

    variables := spec.Variables{
        {Key: "KUBERNETES_NODE_SELECTOR_ROLE", Value: "role=production"},
    }

    o, err := createOverwrites(config, variables, buildlogger.New(nil, "", buildlogger.Options{}))
    assert.NoError(t, err)

    // Expected (secure) behavior: baseline "role" must remain "ci"
    assert.Equal(t, "ci", o.nodeSelector["role"],
        "job-supplied overwrite must not be able to replace the admin-configured baseline node selector key")
}
```
Running this against the current implementation fails: `o.nodeSelector["role"]` becomes `"production"`, demonstrating that a job can override the admin-configured `role=ci` isolation selector.

### Citations

**File:** executors/kubernetes/overwrites.go (L219-230)
```go
	o.nodeSelector, err = o.evaluateMapOverwrite(
		"NodeSelector",
		config.NodeSelector,
		config.NodeSelectorOverwriteAllowed,
		variables,
		NodeSelectorOverwriteVariablePrefix,
		logger,
		splitMapOverwrite,
	)
	if err != nil {
		return nil, err
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
