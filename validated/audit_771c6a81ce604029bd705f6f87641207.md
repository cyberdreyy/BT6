### Title
Unauthenticated LDAP bind with empty password treated as successful login - (File: core/sessions/ldapauth/ldap.go)

### Summary
`CreateSession` in `core/sessions/ldapauth/ldap.go` forwards the user-supplied `sr.Password` directly to `conn.Bind(searchBaseDN, sr.Password)` without ever validating that the password is non-empty. Per RFC 4513 §5.1.2, an LDAP simple bind with a non-empty DN and an empty password is defined as an "unauthenticated bind," which many LDAP directory servers process and return as a success (`resultCode 0`) without validating any secret. Since the code treats `err == nil` from `Bind` as proof of valid credentials, an attacker who knows or guesses a valid user email can obtain a fully authenticated session by submitting an empty password.

### Finding Description
The HTTP entrypoint `SessionsController.Create` in [1](#0-0)  binds the JSON body into `clsessions.SessionRequest` and passes it unmodified to `sc.App.AuthenticationProvider().CreateSession(ctx, sr)`; there is no check anywhere in this handler that `sr.Password` is non-empty or non-whitespace before the call reaches the configured authentication provider.

When the node is configured to use the LDAP authentication provider, that call resolves to `ldapAuthenticator.CreateSession`: [2](#0-1) 

Here `sr.Password` — fully attacker-controlled and never checked for emptiness — is passed straight into `conn.Bind(searchBaseDN, sr.Password)`. The `LDAPConn.Bind` interface wraps the standard `go-ldap` `Bind` call [3](#0-2) , which performs a raw LDAP simple bind operation. Per the LDAP protocol (RFC 4513 §5.1.2), a simple bind with a non-empty bind DN and a zero-length password is classified as an "unauthenticated bind"; the specification explicitly warns that many directory implementations will process this successfully (return success) without checking any credential, because it is a distinct, intentionally-unauthenticated operation type — not a failed authenticated bind. `go-ldap`'s `Bind` function does not special-case or reject this; it simply returns `nil` if the server's `resultCode` is success.

Because the code only checks `if err = conn.Bind(...); err != nil`, an unauthenticated-bind success (nil error) is indistinguishable to this code from a real password match. The code then proceeds to call `l.FindUser(ctx, escapedEmail)` to fetch the user's real role/groups and creates a legitimate `ldap_sessions` row and session cookie for that account — with the account's real assigned role (potentially Admin) [4](#0-3) .

No other layer intercepts this: `Create` in `sessions_controller.go` is deliberately unauthenticated (it is the login endpoint), there is no auth middleware protecting it, and no input validation rejects empty/whitespace passwords before the bind is attempted.

### Impact Explanation
If exploitable against the configured directory server (i.e., the directory processes unauthenticated binds as success, which is common default LDAP server behavior per RFC 4513), an unauthenticated attacker who can reach the node's API port and knows/guesses a valid user email (e.g., a known admin email) can obtain a fully authenticated session cookie with that user's real role — including Admin — without any valid password. This matches the "Critical - node takeover" impact class: full admin session enables key export, job/spec manipulation, and unauthorized transaction submission.

### Likelihood Explanation
Exploitability depends on the upstream LDAP server's bind-handling configuration for zero-length passwords (many servers implement RFC 4513's default unauthenticated-bind acceptance unless explicitly hardened to reject it). No credentials, roles, or prior access are required from the attacker beyond network reachability to `POST /sessions` and knowledge of a valid user email (which may be guessable, e.g., a well-known admin address or discoverable via other means). The vulnerable code path itself performs no defense-in-depth check to reject empty passwords before delegating to the directory, so the attack is fully reproducible against any LDAP-backed deployment whose server exhibits the RFC-default unauthenticated-bind behavior.

### Recommendation
In `ldapAuthenticator.CreateSession` (and `TestPassword`), reject empty/whitespace passwords before calling `conn.Bind`, e.g.:
```go
if strings.TrimSpace(sr.Password) == "" {
    return "", errors.New("password must not be empty")
}
```
Apply the same guard at the HTTP boundary in `SessionsController.Create` (`core/web/sessions_controller.go`) as defense in depth, so no authentication provider implementation can be bypassed via unauthenticated LDAP binds.

### Proof of Concept
Table-driven Go test in `core/sessions/ldapauth/ldap_test.go` style, extending the existing mock-based tests (e.g., `TestORM_CreateSession_UpstreamBind`):
1. Mock `LDAPConn.Bind` to return `nil` (success) when called with an empty-string password — simulating an RFC 4513 unauthenticated-bind success from the directory.
2. Call `ldapAuthProvider.CreateSession(ctx, sessions.SessionRequest{Email: <valid user>, Password: ""})` and `Password: "   "`.
3. Assert `CreateSession` returns an error and does NOT return a valid session ID, i.e., add an explicit empty/whitespace check in `CreateSession` and assert the mock `Bind` method is never invoked for such inputs (`mockLdapConnProvider.AssertNotCalled(t, "Bind", ...)`).
4. At the handler level, add a `sessions_controller_test.go` case posting `{"email": "<user>", "password": ""}` to `POST /sessions` and assert HTTP 401/400 and no `Set-Cookie` session header is returned.

### Citations

**File:** core/web/sessions_controller.go (L29-60)
```go
func (sc *SessionsController) Create(c *gin.Context) {
	defer sc.App.WakeSessionReaper()
	ctx := c.Request.Context()
	sc.App.GetLogger().Debugf("TRACE: Starting Session Creation")

	session := sessions.Default(c)
	var sr clsessions.SessionRequest
	if err := c.ShouldBindJSON(&sr); err != nil {
		jsonAPIError(c, http.StatusBadRequest, fmt.Errorf("error binding json %w", err))
		return
	}

	// Does this user have 2FA enabled?
	userWebAuthnTokens, err := sc.App.AuthenticationProvider().GetUserWebAuthn(ctx, sr.Email)
	if err != nil {
		sc.App.GetLogger().Errorf("Error loading user WebAuthn data: %s", err)
		jsonAPIError(c, http.StatusInternalServerError, errors.New("internal Server Error"))
		return
	}

	// If the user has registered MFA tokens, then populate our session store and context
	// required for successful WebAuthn authentication
	if len(userWebAuthnTokens) > 0 {
		sr.SessionStore = sc.sessions
		sr.WebAuthnConfig = sc.App.GetWebAuthnConfiguration()
	}

	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
		return
	}
```

**File:** core/sessions/ldapauth/ldap.go (L405-411)
```go
	// Attempt to LDAP Bind with user provided credentials
	escapedEmail := ldap.EscapeFilter(strings.ToLower(sr.Email))
	searchBaseDN := fmt.Sprintf("%s=%s,%s,%s", l.config.BaseUserAttr(), escapedEmail, l.config.UsersDN(), l.config.BaseDN())
	if err = conn.Bind(searchBaseDN, sr.Password); err != nil {
		l.lggr.Infof("Error binding user authentication request in LDAP Bind: %v", err)
		returnErr = errors.New("unable to log in with LDAP server. Check credentials")
	}
```

**File:** core/sessions/ldapauth/ldap.go (L413-456)
```go
	// Bind was successful meaning user and credentials are present in LDAP directory
	// Reuse FindUser functionality to fetch user roles used to create ldap_session entry
	// with cached user email and role
	foundUser, err := l.FindUser(ctx, escapedEmail)
	if err != nil {
		l.lggr.Infof("Successful user login, but error querying for user groups: user: %s, error %v", escapedEmail, err)
		returnErr = errors.New("log in successful, but no assigned groups to assume role")
	}

	isLocalUser := false
	if returnErr != nil {
		// Unable to log in against LDAP server, attempt fallback local auth with credentials, case of local CLI Admin account
		// Successful local user sessions can not be managed by the upstream server and have expiration handled by the reaper sync module
		foundUser, returnErr = l.localLoginFallback(ctx, sr)
		isLocalUser = true
	}

	// If err is still populated, return
	if returnErr != nil {
		return "", returnErr
	}

	l.lggr.Infof("Successful LDAP login request for user %s - %s", sr.Email, foundUser.Role)

	// Save session, user, and role to database. Given a session ID for future queries, the LDAP server will not be queried
	// Sessions are set to expire after the duration + creation date elapsed, and are synced on an interval against the upstream
	// LDAP server
	session := sessions.NewSession()
	_, err = l.ds.ExecContext(
		ctx,
		"INSERT INTO ldap_sessions (id, user_email, user_role, localauth_user, created_at) VALUES ($1, $2, $3, $4, now())",
		session.ID,
		strings.ToLower(sr.Email),
		foundUser.Role,
		isLocalUser,
	)
	if err != nil {
		l.lggr.Errorf("unable to create new session in ldap_sessions table %v", err)
		return "", fmt.Errorf("error creating local LDAP session: %w", err)
	}

	l.auditLogger.Audit(audit.AuthLoginSuccessNo2FA, map[string]any{"email": sr.Email})

	return session.ID, nil
```

**File:** core/sessions/ldapauth/client.go (L21-24)
```go
type LDAPConn interface {
	Search(searchRequest *ldap.SearchRequest) (*ldap.SearchResult, error)
	Bind(username string, password string) error
	Close() (err error)
```
