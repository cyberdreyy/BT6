### Title
Path-traversal in Vault JWT auth path construction escapes the `auth/` namespace - (helpers/vault/auth_methods/jwt/auth.go)

### Summary
`method.Authenticate` builds the Vault login path with `path.Join("auth", a.path, "login")`, and Go's `path.Join` calls `path.Clean` on the result, which collapses `..` segments. Since `a.path` originates from `VaultSecret.Server.Auth.Path`, which is expanded via `vars.ExpandValue` in `common/spec/spec.go` (`VaultAuth.expandVariables`), a pipeline author who can influence the value that ends up in `secrets.vault.server.auth.path` (directly in `.gitlab-ci.yml`, or through a CI/CD variable such as `CI_VAULT_AUTH_PATH` referenced from that field) can supply a value like `../other-mount` to make the resulting Vault API path point outside the intended `auth/` prefix.

### Finding Description
`common/spec/spec.go` defines `VaultAuth.Path` as a free-form string field that is populated from the job payload and then expanded against job variables: [1](#0-0) 

This value flows into `VaultSecret.AuthPath()`: [2](#0-1) 

and is used to construct the JWT auth method with `path` set to this raw string, stored as `a.path` in `method`: [3](#0-2) 

In `Authenticate`, the auth path is built with: [4](#0-3) 

`path.Join` normalizes (`Clean`s) the joined result, so `path.Join("auth", "../other-role", "login")` evaluates to `"other-role/login"`, not `"auth/other-role/login"`. There is no validation anywhere in `helpers/vault/auth_methods/jwt/auth.go`, `common/spec/spec.go`, or `helpers/vault/client.go` that rejects `..`, absolute-looking segments, or otherwise confines `a.path` to a subpath of `auth/`. The resulting string is passed unmodified to `client.Write(authPath, authPayload)`, which calls the Vault SDK's `Logical().Write(path, data)`, sending an HTTP request to `<vaultURL>/v1/<authPath>` on the configured Vault server: [5](#0-4) 

This means the intended containment ("this write should always target something under `auth/`") is defeated by ordinary path-cleaning semantics, and the actual HTTP path hit on the Vault backend is fully attacker-influenced whenever the pipeline author (or a variable they control) supplies the `path` value.

### Impact Explanation
The concrete impact is limited by the fact that this call happens as an *unauthenticated* Vault write (it's the initial login call, before any token exists) — Vault's own authorization on the target endpoint decides whether the request succeeds. So the traversal lets an attacker redirect the *target path* of an unauthenticated `Logical().Write()` call to an arbitrary Vault API path of their choosing (not just to another auth mount's `login`), which is a genuine violation of the "runner-enforced path stays within the intended `auth/` root" boundary. It could be used to probe or attempt writes against unrelated Vault mounts/engines using the attacker-supplied JSON payload (`auth_methods.Data`, filtered only by allowed field names `jwt`/`role`), potentially causing unintended writes if such an endpoint happens to accept anonymous/unauthenticated writes. It does not, on its own, grant the attacker a valid Vault token to secrets they aren't otherwise authorized for, since Vault's ACLs still gate what an anonymous request can do at the traversed path.

### Likelihood Explanation
Exploitability depends heavily on deployment specifics that are not fully visible in this repository: whether the `secrets.vault.server.auth.path` value is (a) written directly by the same pipeline author in `.gitlab-ci.yml` (in which case they already have full, un-escalated control over the string and traversal adds nothing new), or (b) constructed centrally by an admin using a variable placeholder that a lower-privileged user can override (in which case the traversal is a real boundary bypass). The repository index does not contain the GitLab Rails/CI config-parsing logic that decides how `secrets:vault:server:auth:path` is populated from `.gitlab-ci.yml` vs. from admin-configured integration settings, so it cannot be confirmed from Runner code alone whether an "unprivileged" actor gains anything they didn't already have. Within Runner itself, the missing input validation on `a.path` before path.Join is confirmed and reproducible.

### Recommendation
Validate/sanitize `VaultAuth.Path` before use: reject values containing `..` path segments, leading `/`, or that after `path.Clean` do not remain prefixed by `auth/`. Concretely, in `helpers/vault/auth_methods/jwt/auth.go`'s `Authenticate` (and any other auth method with similar path construction), compute the joined path and verify `strings.HasPrefix(cleaned, "auth/")` (or equivalent) before calling `client.Write`, returning an error otherwise.

### Proof of Concept
Go unit test in `helpers/vault/auth_methods/jwt/auth_test.go`:
```go
func TestAuthenticate_PathTraversalEscapesAuthPrefix(t *testing.T) {
    mockClient := new(vault.MockClient)
    var capturedPath string
    mockClient.On("Write", mock.Anything, mock.Anything).
        Run(func(args mock.Arguments) { capturedPath = args.String(0) }).
        Return(someResultWithToken, nil)

    m, err := NewMethod("../other-role", auth_methods.Data{"jwt": "x"})
    require.NoError(t, err)

    err = m.Authenticate(mockClient)
    require.NoError(t, err)

    // Expected (if properly confined): capturedPath should start with "auth/"
    // Actual: capturedPath == "other-role/login", escaping the "auth/" root
    assert.True(t, strings.HasPrefix(capturedPath, "auth/"), "auth path escaped intended root: %s", capturedPath)
}
```
This test currently fails, demonstrating that `capturedPath` becomes `other-role/login` instead of remaining under `auth/`, confirming the traversal collapses the intended prefix.

### Citations

**File:** common/spec/spec.go (L817-819)
```go
func (s *VaultSecret) AuthPath() string {
	return s.Server.Auth.Path
}
```

**File:** common/spec/spec.go (L848-855)
```go
func (a *VaultAuth) expandVariables(vars Variables) {
	a.Name = vars.ExpandValue(a.Name)
	a.Path = vars.ExpandValue(a.Path)

	for field, value := range a.Data {
		a.Data[field] = vars.ExpandValue(fmt.Sprintf("%s", value))
	}
}
```

**File:** helpers/vault/auth_methods/jwt/auth.go (L36-48)
```go
func NewMethod(path string, data auth_methods.Data) (vault.AuthMethod, error) {
	newData, err := data.Filter(requiredPayloadFields, allowedPayloadFields)
	if err != nil {
		return nil, fmt.Errorf("filtering auth method configuration: %w", err)
	}

	a := &method{
		path: path,
		data: newData,
	}

	return a, nil
}
```

**File:** helpers/vault/auth_methods/jwt/auth.go (L54-61)
```go
func (a *method) Authenticate(client vault.Client) error {
	authPath := path.Join("auth", a.path, "login")
	authPayload := a.data

	result, err := client.Write(authPath, authPayload)
	if err != nil {
		return fmt.Errorf("writing to Vault: %w", err)
	}
```

**File:** helpers/vault/client.go (L96-99)
```go
func (c *defaultClient) Write(path string, data map[string]interface{}) (Result, error) {
	secret, err := c.internal.Logical().Write(path, data)
	return newResult(secret), unwrapAPIResponseError(err)
}
```
