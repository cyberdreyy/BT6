Confirmed: `SessionRequest.Password` has no `binding:"required"` tag and no empty/whitespace validation exists anywhere in the chain from `c.ShouldBindJSON(&sr)` in `Create` down to the LDAP bind call. [1](#0-0) [2](#0-1) 

### Title
LDAP CreateSession accepts empty password as authenticated via unauthenticated bind - (File: core/sessions/ldapauth/ldap.go)

### Summary
`ldapAuthenticator.CreateSession` forwards the raw, unvalidated `SessionRequest.Password` directly into `conn.Bind(searchBaseDN, sr.Password)` with no check for an empty or whitespace-only password. Per RFC 4513 §5.1.2, an LDAP simple bind with a non-empty DN and an empty password is defined as an "unauthenticated bind" and many directory servers return success (no error) for it unless the server operator has explicitly disabled unauthenticated binds, meaning the code's `err == nil` success path can be reached without verifying any credential.

### Finding Description
The reachable path is: `POST /sessions` → `SessionsController.Create` (`core/web/sessions_controller.go:29-68`) → `sc.App.AuthenticationProvider().CreateSession(ctx, sr)`. `Create` only checks that the JSON body binds successfully; it performs zero validation on `sr.Password` (`core/web/sessions_controller.go:36-39`). [3](#0-2) 

When the configured `AuthenticationProvider` is the LDAP provider, `CreateSession` in `core/sessions/ldapauth/ldap.go` builds the DN from the attacker-supplied email and calls `conn.Bind(searchBaseDN, sr.Password)` with no prior check that `sr.Password` is non-empty/non-whitespace: [4](#0-3) 

If the LDAP server treats this as an RFC 4513-compliant unauthenticated bind and returns `nil`, `returnErr` stays `nil`, and the code proceeds to `FindUser` to resolve the user's role and then commits a new row into `ldap_sessions`, returning a valid session ID that grants that user's full role (including Admin, if the guessed/known email belongs to an admin) [5](#0-4) . Note the attacker only needs to know/guess a valid email (which may be enumerable or a well-known default admin address) — no actual password knowledge is required.

This differs from the local (`localauth`) authentication provider, which uses bcrypt-based `CheckPasswordHash` comparison and is not vulnerable to this specific unauthenticated-bind issue [6](#0-5) . The vulnerability is specific to the LDAP-backed authentication provider path, which is an optional/configurable feature (`WebServer.LDAP`).

### Impact Explanation
If an operator has configured the LDAP authentication provider, an unauthenticated attacker who reaches the node's API port and knows or guesses a valid directory email (e.g., a default/service admin account) can log in with an empty password and obtain a full authenticated session with that user's role — up to Admin. This matches the "Critical - node takeover" impact class: unauthenticated attacker gains admin control, enabling key export and unauthorized transaction submission, contingent on the LDAP server's own default unauthenticated-bind behavior not being disabled by the operator.

### Likelihood Explanation
Preconditions: the node must be configured to use the LDAP authentication provider (`WebServer.LDAP`), and the backing LDAP directory server must permit unauthenticated binds (a common default in many LDAP implementations, e.g., OpenLDAP prior to explicit `olcDisallows: bind_anon` hardening). Given those preconditions, the attack requires no credentials — just a known/guessable email and an empty password field, and it is trivially repeatable/scriptable via repeated POST /sessions requests. This is a real gap in the application code: it never enforces "reject empty password before contacting the directory," relying entirely on directory-side hardening that is not guaranteed.

### Recommendation
In `ldapAuthenticator.CreateSession` (and `TestPassword`) in `core/sessions/ldapauth/ldap.go`, explicitly reject requests where `strings.TrimSpace(sr.Password) == ""` before calling `conn.Bind`, returning an authentication error immediately. Additionally add `binding:"required"` (or equivalent non-empty validation) on `SessionRequest.Password` in `core/sessions/session.go`, and consider auditing/blocking this failure mode with `audit.AuthLoginFailedPassword`.

### Proof of Concept
Go handler/unit-level test plan (table-driven) in `core/sessions/ldapauth/ldap_test.go`:
1. Set up `mockLdapClient`/`mockLdapConnProvider` as in `TestORM_CreateSession_UpstreamBind`.
2. Configure `mockLdapConnProvider.On("Bind", mock.Anything, "").Return(nil)` to simulate a directory that treats empty-password bind as an unauthenticated-bind success (per RFC 4513 default behavior).
3. Call `ldapAuthProvider.CreateSession(ctx, sessions.SessionRequest{Email: user1.Email, Password: ""})` and also with `Password: "   "`.
4. Assert (current behavior, demonstrating the bug) that `err == nil` and a valid session ID is returned — i.e., no error is raised despite no real password verification occurring.
5. After the fix, add the same table test asserting `err != nil` (e.g., `ErrorContains(t, err, "invalid password")`) for empty and whitespace-only passwords, and assert `conn.Bind` was never called (`mockLdapConnProvider.AssertNotCalled(t, "Bind", ...)`) for those inputs.

### Citations

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

**File:** core/web/sessions_controller.go (L34-39)
```go
	session := sessions.Default(c)
	var sr clsessions.SessionRequest
	if err := c.ShouldBindJSON(&sr); err != nil {
		jsonAPIError(c, http.StatusBadRequest, fmt.Errorf("error binding json %w", err))
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

**File:** core/sessions/localauth/orm.go (L159-162)
```go
	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}
```
