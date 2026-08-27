Confirmed: `CreateSession` in `core/sessions/ldapauth/ldap.go` has no explicit check rejecting empty/blank passwords before calling `conn.Bind(searchBaseDN, sr.Password)`, and the auth-facing HTTP handler `SessionsController.Create` in `core/web/sessions_controller.go` also performs no such check before forwarding `sr` to `CreateSession`.### Title
LDAP unauthenticated (empty-password) bind is trusted as valid authentication - (File: core/sessions/ldapauth/ldap.go)

### Summary
`ldapAuthenticator.CreateSession` forwards `sr.Password` directly into `conn.Bind(searchBaseDN, sr.Password)` with no check for an empty/blank password before treating a nil bind error as a successful authentication. Per RFC 4513 §5.1.2, a Simple Bind with a non-empty DN and a zero-length password is defined as an "unauthenticated bind," which many LDAP servers (unless explicitly hardened) will accept and return success for, letting an attacker who knows/guesses only a victim's email obtain a valid session.

### Finding Description
In `CreateSession` [1](#0-0) , the code builds `searchBaseDN` from the attacker-supplied `sr.Email` and calls `conn.Bind(searchBaseDN, sr.Password)`. If `err == nil`, the code proceeds to treat the login as authenticated: it calls `FindUser` to resolve a role and, on success, inserts a new row into `ldap_sessions` and returns a valid session ID [2](#0-1) .

There is no check anywhere in this path — neither in `CreateSession` nor in the HTTP entry point `SessionsController.Create` [3](#0-2)  — that rejects an empty or blank `sr.Password` before it is handed to `conn.Bind`. The `LDAPConn.Bind` interface method is a direct passthrough to the go-ldap library's Simple Bind mechanics [4](#0-3) , which does not client-side reject an empty password — the accept/reject decision is delegated entirely to the upstream LDAP server's configuration.

Per RFC 4513 §5.1.2, a bind with a non-empty DN and an empty password is explicitly defined as an "unauthenticated bind" and, unless the server has been configured to reject it (many servers default to allowing it, or misconfigured deployments leave it enabled), the bind will succeed with no real credential verification performed. Because the application's `CreateSession` treats any `nil` return from `Bind` identically — without distinguishing an authenticated bind from an anonymous/unauthenticated bind — an attacker who only knows a victim's email (no password at all) can obtain a `ldap_sessions` row and a valid session cookie/session ID scoped to the victim's email and role.

### Impact Explanation
This maps to an authentication bypass / unauthorized session creation impact: an unauthenticated attacker who knows or guesses a valid user's email can, without any password, obtain a session assuming that user's role (up to Admin, depending on LDAP group membership) if the upstream LDAP server permits unauthenticated binds. This is a full account-takeover-class vulnerability contingent on upstream server behavior.

### Likelihood Explanation
The precondition is that the deployment's configured LDAP server accepts unauthenticated/anonymous binds for the given DN — this is a server-side configuration matter, not purely a misconfiguration on the Chainlink node's part, since the go-ldap library and RFC 4513 both document this as a well-known behavioral class that application code is expected to defensively guard against by rejecting empty passwords client-side. Many LDAP deployments (e.g., unhardened OpenLDAP setups) allow this by default. The attacker needs zero credentials — only a known/guessable victim email — making this a very low-cost, easily repeatable exploit path once the precondition (server behavior) is met.

### Recommendation
In `CreateSession` (and `TestPassword`), explicitly reject empty/whitespace-only passwords before calling `conn.Bind`, e.g.:
```go
if strings.TrimSpace(sr.Password) == "" {
    return "", errors.New("password required")
}
```
This prevents relying on upstream server behavior to reject RFC 4513 unauthenticated binds.

### Proof of Concept
Go unit test in `core/sessions/ldapauth/ldap_test.go`:
1. Mock `LDAPConn.Bind` to return `nil` (success) whenever called with an empty-string password argument, regardless of DN, simulating a permissive LDAP server performing an unauthenticated bind.
2. Call `ldapAuthProvider.CreateSession(ctx, sessions.SessionRequest{Email: "victim@example.com", Password: ""})`.
3. Assert (post-fix) that `CreateSession` returns an error and does NOT insert a row into `ldap_sessions` for `victim@example.com` — i.e., add an explicit assertion that the returned session ID is empty and an error like "password required" is returned, instead of the current behavior where the mocked nil `Bind` result flows through to a successful `ldap_sessions` insert.

### Citations

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

**File:** core/sessions/ldapauth/ldap.go (L440-456)
```go
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

**File:** core/sessions/ldapauth/client.go (L20-25)
```go
// Wrapper for ldap connection and mock testing, implemented by *ldap.Conn
type LDAPConn interface {
	Search(searchRequest *ldap.SearchRequest) (*ldap.SearchResult, error)
	Bind(username string, password string) error
	Close() (err error)
}
```
