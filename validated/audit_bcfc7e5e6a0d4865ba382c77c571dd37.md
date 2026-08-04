### Title
Unsanitized `VaultAuth.Path` allows job-controlled path traversal outside the intended `auth/` mount namespace - ([File: helpers/vault/auth_methods/jwt/auth.go])

### Summary
The JWT auth method builds the Vault login path with `path.Join("auth", a.path, "login")` without validating that `a.path` stays inside the `auth/` namespace. Because `a.path` originates from `spec.VaultAuth.Path`, which is fully attacker-influenced CI configuration expanded through job variables, a job author can supply `..`-segments that `path.Join`'s implicit `Clean()` resolves into a path that walks outside the `auth/` prefix, causing the Runner to issue its (initial, pre-authentication) Vault `Write` request against an arbitrary path on the same Vault server instead of the intended auth-method mount.

### Finding Description
`method.Authenticate` in [1](#0-0)  computes `authPath := path.Join("auth", a.path, "login")` and immediately calls `client.Write(authPath, authPayload)`. `a.path` is set verbatim from the `path` argument passed to `NewMethod` [2](#0-1) , with no character or segment validation performed anywhere in this package.

The value flows from CI job configuration: `spec.VaultAuth.Path` is expanded via `vars.ExpandValue` in `(*VaultAuth).expandVariables` [3](#0-2) , exposed via `VaultSecret.AuthPath()` [4](#0-3) , and consumed by `defaultVault.prepareAuthMethodAdapter`, which passes it straight into the registered auth-method factory (`jwt.NewMethod`) with zero sanitation [5](#0-4) . This value is job-controlled CI configuration (`secrets.<NAME>.vault.server.auth.path`), so an unprivileged pipeline author who can edit `.gitlab-ci.yml` (or otherwise influence variables referenced by the path template) fully controls `a.path`.

`path.Join` calls `path.Clean` on the joined string. For a relative path, leading `..` segments that would go "above" what's given are preserved literally rather than being dropped, e.g. `path.Join("auth", "../../secret_engines/other-tenant", "login")` evaluates to `"../secret_engines/other-tenant/login"` — the `auth` segment is fully cancelled and the result is a relative path starting with `..`, no longer prefixed by `auth/`. This string is then handed to `client.Write`, which forwards it to the underlying OpenBao/Vault API client's `Logical().Write(path, data)` [6](#0-5) . The Vault API client itself joins this path onto the request URL (again typically via `path.Join`-style logic), and because this second join operates on an absolute base, any leading `..` segments are dropped instead of preserved — collapsing the final request path to something like `/secret_engines/other-tenant/login`, i.e., completely outside the `auth/` mount namespace that the Runner intended to restrict itself to.

No existing check in this code path (`NewMethod`, `Authenticate`, `prepareAuthMethodAdapter`, or `VaultAuth.expandVariables`) validates that the resulting path is confined to `auth/`. The only sanitization present, `auth_methods.Data.Filter`, only restricts the *payload keys* (`jwt`, `role`), not the *path*.

### Impact Explanation
An unprivileged CI job author can force the Runner to send its Vault JWT-login `Write` request (containing attacker-supplied JWT/role payload values, since `VaultAuth.Data` is also expanded from job variables) to an arbitrary path on the configured Vault server, outside the `auth/` namespace the administrator intended the Runner's Vault integration to touch. This breaks the invariant that job-controlled input must not let a request escape its intended mount/namespace boundary. Concretely this is a path-confusion/SSRF-style primitive against the Vault server reachable by the Runner: the request can be steered at any other mount or system path (e.g. a different auth backend belonging to another tenant/team, or non-auth engine paths), with attacker-chosen body content. The severity of what an attacker can achieve at the mis-routed path (reading data, triggering side effects) depends on that target path's own access-control configuration, but the Runner provides no boundary enforcement of its own, which is the actual bug being scoped here.

### Likelihood Explanation
This is directly reachable by any pipeline author able to configure the `secrets:` block (or influence the variables it interpolates) for a job that uses Vault secrets — no special runner or GitLab admin privileges are needed. `VaultAuth.Path` is plain string CI configuration expanded through ordinary variable expansion, with no allow-list or path-confinement check anywhere along `spec.go` → `service/vault.go` → `jwt/auth.go`. The traversal primitive (`path.Join` collapsing `..`) is deterministic and trivially reproducible.

### Recommendation
Validate/sanitize `a.path` (and any other auth-method `path` inputs) before use: reject values containing `..` segments, leading `/`, or otherwise ensure with `filepath`/`path` cleaning plus an explicit prefix check that the final `authPath` still has `auth/` as its first path element after cleaning. This check should live in `jwt.NewMethod`/`Authenticate` (and any other auth-method implementations sharing this pattern) or centrally in `service.defaultVault.prepareAuthMethodAdapter` before the factory is invoked.

### Proof of Concept
Unit test extending `helpers/vault/auth_methods/jwt/auth_test.go`'s `TestJWTAuth_Authenticate_Token` pattern:
```go
func TestJWTAuth_Authenticate_PathTraversal(t *testing.T) {
    maliciousPath := "../../secret_engines/other-tenant"
    // path.Join("auth", maliciousPath, "login") resolves to a path
    // outside the "auth/" prefix instead of "auth/../../secret_engines/other-tenant/login"
    computed := path.Join("auth", maliciousPath, "login")
    assert.False(t, strings.HasPrefix(computed, "auth/"),
        "authPath escaped the auth/ mount namespace: %s", computed)

    clientMock := vault.NewMockClient(t)
    clientMock.On("Write", computed, mock.Anything).
        Return(nil, assert.AnError).Once()

    auth, err := NewMethod(maliciousPath, map[string]interface{}{"jwt": "x"})
    require.NoError(t, err)
    _ = auth.Authenticate(clientMock)
    clientMock.AssertExpectations(t)
}
```
Expected assertion: `computed` does not start with `"auth/"`, demonstrating the Runner will issue a `Write` outside the intended mount namespace whenever `a.path` (i.e., job-controlled `secrets.<NAME>.vault.server.auth.path`) contains traversal segments.

### Citations

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

**File:** helpers/vault/service/vault.go (L83-95)
```go
func (v *defaultVault) prepareAuthMethodAdapter(authDetails Auth) (vault.AuthMethod, error) {
	authFactory, err := auth_methods.GetFactory(authDetails.AuthName())
	if err != nil {
		return nil, fmt.Errorf("initializing auth method factory: %w", err)
	}

	auth, err := authFactory(authDetails.AuthPath(), authDetails.AuthData())
	if err != nil {
		return nil, fmt.Errorf("initializing auth method adapter: %w", err)
	}

	return auth, nil
}
```

**File:** helpers/vault/client.go (L96-99)
```go
func (c *defaultClient) Write(path string, data map[string]interface{}) (Result, error) {
	secret, err := c.internal.Logical().Write(path, data)
	return newResult(secret), unwrapAPIResponseError(err)
}
```
