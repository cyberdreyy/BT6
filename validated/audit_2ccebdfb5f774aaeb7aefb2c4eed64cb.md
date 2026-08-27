### Title
Authentication error handling in `AuthenticateByToken` leaks distinct LDAP config-state error messages to unauthenticated attackers - ([File: core/web/auth/auth.go])

### Summary
`AuthenticateByToken` only converts `FindUserByAPIToken` errors to the generic `auth.ErrorAuthFailed` when the underlying error is `sql.ErrNoRows` or `clsessions.ErrUserSessionExpired`; all other errors are returned unmodified and rendered back to the client. The LDAP authenticator's `FindUserByAPIToken` returns a distinct, literal error `"API token is not enabled "` when `UserApiTokenEnabled` is `false`, which is a different code path from the "no token found" case, allowing an unauthenticated caller to distinguish server-side LDAP API-token configuration state from a generic invalid-credential response.

### Finding Description
In `AuthenticateByToken` (`core/web/auth/auth.go:78-112`), the call to `authr.FindUserByAPIToken(ctx, token.AccessKey)` is only normalized to the generic `auth.ErrorAuthFailed` for two specific error values: [1](#0-0) 
Any other error returned by the `Authenticator` implementation is passed straight through and ultimately rendered via `jsonAPIError` in the `Authenticate` middleware (`core/web/auth/auth.go:157-175`).

The LDAP implementation of `FindUserByAPIToken` (`core/sessions/ldapauth/ldap.go:205-236`) returns `errors.New("API token is not enabled ")` when `l.config.UserApiTokenEnabled()` is `false`, which is neither `sql.ErrNoRows` nor `clsessions.ErrUserSessionExpired`: [2](#0-1) 
When the token isn't found or is expired, the DB query returns `sql.ErrNoRows` (from `GetContext`) or, if found-but-expired, `sessions.ErrUserSessionExpired`: [3](#0-2) 

An unauthenticated attacker sending any `GET` request to an authenticated route with arbitrary `X-API-KEY`/`X-API-SECRET` headers against an LDAP-mode node will therefore receive:
- A response derived from `auth.ErrorAuthFailed` (generic 401) when the feature is enabled but the token doesn't exist/is expired.
- A response containing the literal, un-normalized string `"API token is not enabled "` when the feature is disabled.

This lets the attacker infer the value of the `UserApiTokenEnabled` LDAP configuration flag without any credentials, purely from response content differences.

### Impact Explanation
This is a low-severity information disclosure: it reveals a single boolean configuration flag (`UserApiTokenEnabled`) of the LDAP authenticator to an unauthenticated attacker. It does not by itself grant authentication bypass, privilege escalation, credential/key disclosure, or allow acting on another user's resources — it only aids reconnaissance (e.g., attacker learns whether attempting further LDAP API-token brute force is worthwhile). This maps to a minor/informational finding under Chainlink's bounty program (information disclosure with no direct exploit path to fund movement or account takeover).

### Likelihood Explanation
No preconditions or credentials are required — a single unauthenticated HTTP request with arbitrary header values against an LDAP-configured node is sufficient, and the behavior is fully deterministic and repeatable. The only prerequisite is that the node be deployed with LDAP authentication (`ldapAuthenticator`) rather than the default local session/token authenticator, which is a non-default, enterprise-only configuration.

### Recommendation
In `AuthenticateByToken` (`core/web/auth/auth.go`), normalize *all* non-nil errors from `FindUserByAPIToken` to `auth.ErrorAuthFailed` before returning (or at minimum, treat the LDAP "token not enabled" condition identically to "not found"), so the generic 401 body/status is always emitted regardless of internal cause. Any internal error detail should only be logged server-side, not exposed in the response body.

### Proof of Concept
Add a Go handler-level test in `core/web/auth` (or an integration test using the LDAP authenticator test doubles):
1. Construct a stub `Authenticator` whose `FindUserByAPIToken` returns `errors.New("API token is not enabled ")` (simulating `UserApiTokenEnabled=false`).
2. Construct a second stub `Authenticator` whose `FindUserByAPIToken` returns `sql.ErrNoRows` (simulating `UserApiTokenEnabled=true` with an unknown token).
3. Run both through `auth.Authenticate(store, auth.AuthenticateByToken)` wired to a `gin` test router, sending `GET /any-protected-route` with `X-API-KEY: randomvalue`, `X-API-SECRET: randomvalue`.
4. Assert: both responses return `http.StatusUnauthorized`, and assert the response bodies are byte-for-byte identical (both should contain only the generic `ErrorAuthFailed` message). The test should currently FAIL because case 1 leaks `"API token is not enabled "` while case 2 returns the generic auth-failed message — demonstrating the distinguishable response.

### Citations

**File:** core/web/auth/auth.go (L93-99)
```go
	user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
			return auth.ErrorAuthFailed
		}
		return err
	}
```

**File:** core/sessions/ldapauth/ldap.go (L205-208)
```go
func (l *ldapAuthenticator) FindUserByAPIToken(ctx context.Context, apiToken string) (sessions.User, error) {
	if !l.config.UserApiTokenEnabled() {
		return sessions.User{}, errors.New("API token is not enabled ")
	}
```

**File:** core/sessions/ldapauth/ldap.go (L218-230)
```go
	err := l.ds.GetContext(ctx, &foundUserToken,
		"SELECT user_email, user_role, created_at + $2 >= now() as valid FROM ldap_user_api_tokens WHERE token_key = $1",
		apiToken, l.config.UserAPITokenDuration().Duration(),
	)
	if err != nil {
		return sessions.User{}, err
	}
	if !foundUserToken.Valid { // API Token expired, purge
		if _, execErr := l.ds.ExecContext(ctx, "DELETE FROM ldap_user_api_tokens WHERE token_key = $1", apiToken); execErr != nil {
			l.lggr.Errorf("error purging stale ldap API token session: %v", execErr)
		}
		return sessions.User{}, sessions.ErrUserSessionExpired
	}
```
