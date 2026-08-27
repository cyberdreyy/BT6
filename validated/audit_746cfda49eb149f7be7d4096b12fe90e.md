### Title
LDAP Unauthenticated ("Zero-Length Password") Bind Allows Full Authentication Bypass for Any Directory User - ([File: core/sessions/ldapauth/ldap.go])

### Summary
`ldapAuthenticator.CreateSession` calls `conn.Bind(searchBaseDN, sr.Password)` with an attacker-supplied password and never rejects an empty password before doing so. Per RFC 4513 §5.1.2, most LDAP servers treat a bind with a zero-length password as an "unauthenticated bind," which many server configurations accept as a successful bind regardless of the DN's validity. An attacker who simply POSTs `{"email":"<victim-or-admin-email>","password":""}` to `/sessions` can therefore obtain a fully authenticated session for any user known to the directory, including admins, without knowing their password.

### Finding Description
`SessionsController.Create` (`core/web/sessions_controller.go`) binds the request JSON directly into `clsessions.SessionRequest` and passes it unmodified to `AuthenticationProvider().CreateSession` [1](#0-0) . `sessions.SessionRequest` places no validation on `Password`, allowing it to be an empty string [2](#0-1) .

In `ldapAuthenticator.CreateSession`, the code builds the bind DN from the attacker-controlled (escaped) email and calls `conn.Bind(searchBaseDN, sr.Password)` with no check that `sr.Password` is non-empty: [3](#0-2) 

If the upstream LDAP/AD server permits unauthenticated binds (the RFC 4513 default unless explicitly disabled via server policy, e.g., `disallow-unauth-binds` in OpenLDAP), `conn.Bind` returns `nil` (success) even though the caller supplied no valid credential — this is a well-known "LDAP unauthenticated bind" pitfall independent of whether the target DN even exists. Since `err == nil`, `returnErr` stays `nil`, and the code proceeds to `l.FindUser(ctx, escapedEmail)`, which queries the directory purely for group membership and returns the real role assigned to that email [4](#0-3) . A full `ldap_sessions` row is then created and the session ID is returned to the caller with that user's actual role: [5](#0-4) .

No component in the reachable path — `SessionsController.Create`, `SessionRequest`, or `CreateSession` — rejects an empty `Password` before it reaches `conn.Bind`, so this authentication check depends entirely on the LDAP server's own configuration to reject unauthenticated binds, which is not guaranteed and historically a very common misconfiguration/default.

### Impact Explanation
If the upstream directory allows unauthenticated binds, an unauthenticated network attacker can log in as any user present in the LDAP directory — including users mapped to the Admin RBAC group — by supplying only that user's email and an empty password. This is a full authentication bypass leading to complete node compromise (admin session creation, subsequent job/key/secret access), matching the "authentication bypass leading to node compromise" bounty class.

### Likelihood Explanation
No credentials are required from the attacker beyond knowledge of a valid directory user's email (often discoverable/guessable, e.g. common corporate email formats). The only precondition is that the operator's LDAP server has not explicitly disabled unauthenticated binds — a setting many LDAP/AD deployments leave at default. Chainlink's own code performs no defense-in-depth check to reject empty passwords, so the entire mitigation rests on external LDAP server hardening, which is out of this component's control.

### Recommendation
In `ldapAuthenticator.CreateSession` (and `TestPassword`), explicitly reject empty/zero-length `sr.Password` before calling `conn.Bind`, e.g.:
```go
if sr.Password == "" {
    return "", errors.New("unable to log in with LDAP server. Check credentials")
}
```
This removes reliance on upstream LDAP server configuration to reject RFC 4513 unauthenticated binds.

### Proof of Concept
Go handler/unit test plan for `core/sessions/ldapauth/ldap_test.go`:
1. Configure a mocked `LDAPClient`/`LDAPConn` (as used in existing `ldap_test.go`) where `Bind(dn, "")` returns `nil` (simulating a server permitting unauthenticated bind), regardless of `dn`.
2. Configure `FindUser`'s underlying search mock to return a valid admin-group membership entry for a known victim email (e.g., `admin@example.com`).
3. Call `ldapAuthenticator.CreateSession(ctx, sessions.SessionRequest{Email: "admin@example.com", Password: ""})`.
4. Assert: current code returns a valid non-empty session ID with no error, and a row is inserted into `ldap_sessions` with `user_role = admin` — demonstrating full authentication bypass.
5. After applying the fix (reject empty password), assert `CreateSession` returns an error and no session row is created for the same input.

### Citations

**File:** core/web/sessions_controller.go (L34-60)
```go
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

**File:** core/sessions/session.go (L16-22)
```go
type SessionRequest struct {
	Email          string `json:"email"`
	Password       string `json:"password"`
	WebAuthnData   string `json:"webauthndata"`
	WebAuthnConfig WebAuthnConfiguration
	SessionStore   *WebAuthnSessionStore
}
```

**File:** core/sessions/ldapauth/ldap.go (L404-411)
```go

	// Attempt to LDAP Bind with user provided credentials
	escapedEmail := ldap.EscapeFilter(strings.ToLower(sr.Email))
	searchBaseDN := fmt.Sprintf("%s=%s,%s,%s", l.config.BaseUserAttr(), escapedEmail, l.config.UsersDN(), l.config.BaseDN())
	if err = conn.Bind(searchBaseDN, sr.Password); err != nil {
		l.lggr.Infof("Error binding user authentication request in LDAP Bind: %v", err)
		returnErr = errors.New("unable to log in with LDAP server. Check credentials")
	}
```

**File:** core/sessions/ldapauth/ldap.go (L413-420)
```go
	// Bind was successful meaning user and credentials are present in LDAP directory
	// Reuse FindUser functionality to fetch user roles used to create ldap_session entry
	// with cached user email and role
	foundUser, err := l.FindUser(ctx, escapedEmail)
	if err != nil {
		l.lggr.Infof("Successful user login, but error querying for user groups: user: %s, error %v", escapedEmail, err)
		returnErr = errors.New("log in successful, but no assigned groups to assume role")
	}
```

**File:** core/sessions/ldapauth/ldap.go (L437-456)
```go
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
