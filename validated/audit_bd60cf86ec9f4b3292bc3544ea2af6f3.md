### Title
Vault secret path traversal via `VaultSecret.Path` allows escaping the configured mount prefix - (File: helpers/vault/secret_engines/kv_v2/engine.go)

### Summary
`engine.dataPath` (and `engine.metadataPath`) builds the Vault request path with `path.Join(e.path, "data", p)` where `p` comes directly from `secret.SecretPath()`, i.e. `VaultSecret.Path`, which is expanded from job-controlled CI/CD variables via `VaultSecret.expandVariables` / `Variables.ExpandValue`. Because `path.Join` performs lexical `..` collapsing with no containment check, a job author who controls the value substituted into `Path` can supply `../../<other-path>` segments to make the resulting Vault request path escape the operator-configured mount/engine prefix entirely.

### Finding Description
The call chain is: `resolver.Resolve` (`helpers/secrets/resolvers/vault/resolver.go:38-56`) reads `secret.Path` from `spec.VaultSecret`, which was populated via `s.Path = vars.ExpandValue(s.Path)` in `VaultSecret.expandVariables` (`common/spec/spec.go:805-811`) — this expansion directly substitutes job/pipeline variable values (e.g. a `CI_VAULT_PATH`-style variable) into the path string with no traversal filtering.

The resolver calls `s.GetField(secret, secret)` → `defaultVault.GetField` (`helpers/vault/service/vault.go:97-118`), which calls `engine.Get(secretDetails.SecretPath())`. For `kv-v2`, `engine.Get` (`helpers/vault/secret_engines/kv_v2/engine.go:29-53`) calls `e.dataPath(path)`: [1](#0-0) 
which is `path.Join(e.path, "data", p)` with no validation that the result remains rooted under `e.path`. `path.Join` lexically cleans `..` segments across all supplied components, so a `p` value like `../../otherapp/creds` collapses `e.path/data/../../otherapp/creds` down to `otherapp/creds` — completely outside the operator's mount prefix `e.path`. The same unguarded pattern exists in `metadataPath` (`helpers/vault/secret_engines/kv_v2/engine.go:81-83`) and in the `generic`/`kv-v1` engine's `fullPath` (`helpers/vault/secret_engines/generic/engine.go:40-42`).

No layer in the reachable path — `VaultSecret.expandVariables`, `resolver.Resolve`, `defaultVault.GetField`/`getSecretEngine`, or the engine's `dataPath`/`fullPath` — performs any check that the final joined path stays prefixed by the mount path `e.path`. The only control preventing cross-tenant reads is the Vault ACL policy attached to the runner's authenticated token; if that policy is a wildcard (e.g. `secret/data/*`) rather than pinned to an exact per-project path, the traversal reaches sibling projects' secrets.

### Impact Explanation
An unprivileged pipeline author who can set/override the CI/CD variable feeding `secrets:.vault.path` can cause the runner to request a Vault path outside its own project's intended prefix, resulting in cross-project/tenant secret exfiltration when combined with a permissive (wildcard) Vault policy on the runner's authentication role. This is a real path-containment bug regardless of whether the ACL happens to be tight in a particular deployment — the Runner itself performs no defense-in-depth check.

### Likelihood Explanation
The exploit is trivial to trigger: any job author who can set/override the variable interpolated into `secrets:<name>.vault.path` (a normal `.gitlab-ci.yml` capability, or CI/CD variable override depending on how the pipeline authors configured the path) can supply `../../other-project/creds`. It requires the additional precondition of a Vault ACL that isn't scoped tightly per path, which is a plausible and common misconfiguration (wildcard policies over a shared mount are frequently used for multi-tenant simplicity). The bug is 100% repeatable and deterministic given `path.Join`'s well-defined lexical cleaning behavior.

### Recommendation
In `engine.dataPath`/`engine.metadataPath` (kv_v2) and `engine.fullPath` (generic/kv-v1), validate that the resulting joined path remains rooted under `e.path` after cleaning — e.g. compute `path.Join(e.path, "data", p)` then verify it still has `e.path` (or `e.path + "/"`) as a prefix (after `path.Clean`), returning an error otherwise. Additionally, consider rejecting `p` values containing `..` path segments before joining, at the `VaultSecret.expandVariables` layer or in the engine `Get`/`Put`/`Delete` entry points, so the check is centralized and applies to all secret engines.

### Proof of Concept
```go
package kv_v2

import "testing"

func TestEngine_DataPath_PreventsTraversal(t *testing.T) {
    e := &engine{path: "myapp"}

    got := e.dataPath("../../otherapp/creds")

    // Bug: got == "otherapp/creds", escaping the "myapp" mount prefix entirely.
    if !strings.HasPrefix(got, "myapp/data/") {
        t.Fatalf("path traversal escaped mount prefix: got %q, want prefix %q", got, "myapp/data/")
    }
}
```
Fuzz variant: fuzz `e.dataPath`/`e.metadataPath` with payloads `../`, `..%2f` (URL-encoded, if ever decoded upstream), absolute paths (`/otherapp/creds`), and null bytes, asserting `strings.HasPrefix(result, path.Clean(e.path)+"/")` always holds.

Integration-level PoC: configure a CI job with `secrets: { TOKEN: { vault: { path: "$CI_VAULT_PATH", field: "value" } } } }` and set `CI_VAULT_PATH=../../other-project/creds`; run against a Vault instance with a wildcard `secret/data/*` policy and mount path `my-project/`; observe the runner reads `secret/data/other-project/creds` instead of `secret/data/my-project/../../other-project/creds` being rejected.

### Citations

**File:** helpers/vault/secret_engines/kv_v2/engine.go (L55-57)
```go
func (e *engine) dataPath(p string) string {
	return path.Join(e.path, "data", p)
}
```
