### Title
LDAP unauthenticated ("anonymous") bind bypasses password verification and grants session for any existing user - ([File: core/sessions/ldapauth/ldap.go])

### Summary
`ldapAuthenticator.CreateSession` passes the client-supplied `sr.Password` directly to `conn.Bind(searchBaseDN, sr.Password)` without first checking that the password is non-empty. Per RFC 4513 §5.1.2, an LDAP bind with a non-empty DN and a zero-length password is defined as an "unauthenticated bind" that most LDAP servers accept as successful (no error returned) without actually validating any credential. Since the code treats any non-error `Bind` result as a fully authenticated login, an attacker who supplies a valid, known/guessable email and an empty `password` field can obtain a valid Chainlink session with that user's role.

### Finding Description
The reachable path is `POST /sessions` handled by `SessionsController.Create` in `core/web/sessions_controller.go`, which is registered unauthenticated in `core/web/router.go` (`unauth.POST("/sessions", sc.Create)`). It binds the JSON body into `sessions.SessionRequest{Email, Password}` with no validation that `Password` is non-empty, then calls `sc.App.AuthenticationProvider().CreateSession(ctx, sr)`.

In `ldapAuthenticator.CreateSession` (`core/sessions/ldapauth/ldap.go:396-457`):
```go
escapedEmail := ldap.EscapeFilter(strings.ToLower(sr.Email))
searchBaseDN := fmt.Sprintf("%s=%s,%s,%s", l.config.BaseUserAttr(), escapedEmail, l.config.UsersDN(), l.config.BaseDN())
if err = conn.Bind(searchBaseDN, sr.Password); err != nil {
    ...
    returnErr = errors.New("unable to log in with LDAP server. Check credentials")
}
```
There is no check anywhere before this call that `sr.Password != ""`. If `sr.Password` is empty, `conn.Bind` performs an RFC 4513 unauthenticated bind. Servers that follow the RFC default (this is the default LDAP protocol behavior unless the server administrator explicitly disables it, e.g. via `disallow bind_anon_cred`/`olcDisallows: bindAnonCred` on OpenLDAP, or an AD policy) return success for this bind regardless of the actual password, because no credential is actually checked. The code then proceeds:
- `returnErr` stays `nil`, so the local-auth fallback is skipped.
- `l.FindUser(ctx, escapedEmail)` is invoked to fetch the target user's real role from the directory (the attacker doesn't need to know the role, just the email).
- A row is inserted into `ldap_sessions` with the found user's real email and real role, and a session ID is returned to the attacker, who now holds a valid session cookie/session ID for that account (e.g., an Admin).

The same missing-empty-password issue also exists in `TestPassword` (`core/sessions/ldapauth/ldap.go:504-514`), which is used elsewhere for credential verification (e.g., changing settings) and shares the identical `conn.Bind(searchBaseDN, password)` pattern.

None of the existing checks intercept this: `sessions_controller.go` `Create` performs no password-length/non-empty validation, and `ldap.EscapeFilter` only prevents LDAP filter injection, it does not address bind semantics.

### Impact Explanation
This is a full authentication bypass: an unauthenticated attacker who knows or enumerates a valid directory email (often predictable, e.g. corporate email format) can obtain a live node session with that user's actual RBAC role (up to Admin), without knowing any password. This maps to the highest Chainlink bounty impact class for node compromise — authentication bypass / unauthorized privileged session creation, potentially enabling job creation/deletion, key management, and fund-moving actions available to Admin-role sessions.

### Likelihood Explanation
- Precondition: the node must be configured to use the LDAP authentication driver (`NewLDAPAuthenticator`) and the upstream LDAP server must permit unauthenticated/anonymous binds for the target DN, which is the RFC 4513 default unless explicitly disabled by the LDAP server operator.
- No credentials, tokens, or special network position are required — a single unauthenticated `POST /sessions` request with `{"email":"<known-or-guessed-email>","password":""}` is sufficient.
- Fully repeatable and scriptable; requires no timing or race conditions.

### Recommendation
In `ldapAuthenticator.CreateSession` (and `TestPassword`), explicitly reject empty passwords before calling `conn.Bind`, e.g.:
```go
if sr.Password == "" {
    return "", errors.New("unable to log in with LDAP server. Check credentials")
}
```
Additionally, consider setting the LDAP client library option to disallow unauthenticated binds outright (many go-ldap consumers guard against this by pre-checking password length), and validate `SessionRequest.Password` non-empty centrally in `SessionsController.Create` as defense in depth.

### Proof of Concept
Handler-level integration test plan (extending `core/sessions/ldapauth/ldap_test.go` style with mocked `LDAPConn`):
1. Set up `ldapAuthenticator` with a mocked `LDAPConn` whose `Bind(dn, password string) error` mock is configured to return `nil` (simulating an RFC 4513 unauthenticated-bind-accepting server) whenever `password == ""`, regardless of `dn`.
2. Configure the mock `Search` call (used by `FindUser`) to return a valid entry with `uniqueMember` mapping the target email to the Admin group CN.
3. Call `ldapAuthProvider.CreateSession(ctx, sessions.SessionRequest{Email: "admin@example.com", Password: ""})`.
4. Assert: no error is returned, a session ID string is returned, and a row exists in `ldap_sessions` with `user_email = admin@example.com` and `user_role = Admin`.
5. Assert `AuthorizedUserWithSession` with that session ID returns the Admin user, confirming full session issuance without any real credential check. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/sessions/ldapauth/ldap.go (L396-411)
```go
func (l *ldapAuthenticator) CreateSession(ctx context.Context, sr sessions.SessionRequest) (string, error) {
	conn, err := l.ldapClient.CreateEphemeralConnection()
	if err != nil {
		return "", errors.New("unable to establish connection to LDAP server with provided URL and credentials")
	}
	defer conn.Close()

	var returnErr error

	// Attempt to LDAP Bind with user provided credentials
	escapedEmail := ldap.EscapeFilter(strings.ToLower(sr.Email))
	searchBaseDN := fmt.Sprintf("%s=%s,%s,%s", l.config.BaseUserAttr(), escapedEmail, l.config.UsersDN(), l.config.BaseDN())
	if err = conn.Bind(searchBaseDN, sr.Password); err != nil {
		l.lggr.Infof("Error binding user authentication request in LDAP Bind: %v", err)
		returnErr = errors.New("unable to log in with LDAP server. Check credentials")
	}
```

**File:** core/sessions/ldapauth/ldap.go (L503-518)
```go
// TestPassword tests if an LDAP login bind can be performed with provided credentials, returns nil if success
func (l *ldapAuthenticator) TestPassword(ctx context.Context, email string, password string) error {
	conn, err := l.ldapClient.CreateEphemeralConnection()
	if err != nil {
		return errors.New("unable to establish connection to LDAP server with provided URL and credentials")
	}
	defer conn.Close()

	// Attempt to LDAP Bind with user provided credentials
	escapedEmail := ldap.EscapeFilter(strings.ToLower(email))
	searchBaseDN := fmt.Sprintf("%s=%s,%s,%s", l.config.BaseUserAttr(), escapedEmail, l.config.UsersDN(), l.config.BaseDN())
	err = conn.Bind(searchBaseDN, password)
	if err == nil {
		return nil
	}
	l.lggr.Infof("Error binding user authentication request in TestPassword call LDAP Bind: %v", err)
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

**File:** core/web/router.go (L207-217)
```go
func sessionRoutes(app chainlink.Application, r *gin.RouterGroup) {
	config := app.GetConfig()
	rl := config.WebServer().RateLimit()
	unauth := r.Group("/", rateLimiter(
		rl.UnauthenticatedPeriod(),
		rl.Unauthenticated(),
	))
	sc := NewSessionsController(app)
	unauth.POST("/sessions", sc.Create)
	auth := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	auth.DELETE("/sessions", sc.Destroy)
```

**File:** core/sessions/session.go (L14-22)
```go
// SessionRequest encapsulates the fields needed to generate a new SessionID,
// including the hashed password.
type SessionRequest struct {
	Email          string `json:"email"`
	Password       string `json:"password"`
	WebAuthnData   string `json:"webauthndata"`
	WebAuthnConfig WebAuthnConfiguration
	SessionStore   *WebAuthnSessionStore
}
```
