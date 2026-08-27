### Title
LDAP-bound identity assumes local admin role via email collision with `users` table due to unauthenticated lookup in `FindUser` - ([File: core/sessions/ldapauth/ldap.go])

### Summary
`ldapAuthenticator.CreateSession` calls `FindUser` immediately after a successful LDAP bind, and `FindUser` unconditionally returns whatever row matches `lower(email)` in the local `users` table *before* ever checking LDAP group membership or verifying the submitted password against that row. Any directory-only account holder who can perform a valid LDAP bind for their own email can obtain the role of a local `users` table entry sharing that same (lowercased) email, without ever proving knowledge of that local record's password.

### Finding Description
`POST /sessions` is unauthenticated and routes to `SessionsController.Create`, which calls `AuthenticationProvider().CreateSession(ctx, sr)` [1](#0-0) [2](#0-1) . When LDAP is the configured provider, this reaches `ldapAuthenticator.CreateSession`.

In `CreateSession`, the attacker's credentials are bound against LDAP using a DN built from their own email: [3](#0-2) 

If the bind succeeds (the attacker is simply a valid directory account, no group required), `returnErr` stays `nil` and `FindUser` is called with that same email: [4](#0-3) 

The root cause is in `FindUser` itself: it checks the local `users` table **first**, and returns that row's role immediately on any match — with no verification that the submitted password corresponds to that local record, and before any LDAP group/role resolution occurs: [5](#0-4) 

Because `escapedEmail`/`sr.Email` is attacker-controlled and only needs to case-insensitively match an existing `users` row, an attacker who owns a valid (ungrouped) LDAP account whose email happens to coincide with a local `users` table entry (e.g., a break-glass/local-admin account provisioned under the same corporate email) will have `checkErr == nil` in `FindUser`, causing it to return that local user's `Role` (potentially `UserRoleAdmin`) with no error. Back in `CreateSession`, `returnErr` remains `nil`, so the `ErrUserNoLDAPGroups`/`localLoginFallback` path (which does perform a password check against the local record via `utils.CheckPasswordHash`) is **never reached**: [6](#0-5) 

The session is then created with the elevated role and `localauth_user = false` (since `isLocalUser` was never set true), persisting the privilege escalation as an `ldap_sessions` row: [7](#0-6) 

No existing check (auth middleware, role wrapper, or the LDAP group mapping logic in `groupSearchResultsToUserRole`) intervenes, because the local-admin lookup at the top of `FindUser` short-circuits before group resolution is ever attempted.

### Impact Explanation
An attacker holding only a valid LDAP bind for their own directory account (no elevated group assignment) can be granted the role of an unrelated local `users` table account merely by email collision, without knowing that account's password. This is a privilege escalation / authentication-identity-confusion vulnerability that can grant local-admin-equivalent sessions to an externally-authenticated, unprivileged identity — matching Chainlink's "authentication bypass / privilege escalation" bounty impact class.

### Likelihood Explanation
Requires: (1) LDAP auth configured, (2) a `users` table row exists whose lowercased email matches an LDAP directory account the attacker legitimately controls (a plausible break-glass/local-CLI-admin setup mirroring a real employee email), and (3) the attacker can bind to LDAP as that account (their own password, no group needed). No local admin credential knowledge, group membership, or elevated role is required — only a valid personal LDAP bind. Given the configuration precondition, exploitation is fully repeatable via a single `POST /sessions` request.

### Recommendation
In `FindUser`, do not return the local `users` table row purely from an email match invoked as part of the post-LDAP-bind flow; require verifying the submitted password against that specific local record (as `localLoginFallback` already does) before assigning its role, or restructure `CreateSession` so the local-admin fallback path is only reachable through `localLoginFallback`'s password-hash check, never through the unconditional early-return branch of `FindUser`. Additionally, resolve LDAP group membership before ever consulting the local `users` table for a bound LDAP identity, and reject with a hard failure when `ErrUserNoLDAPGroups` occurs unless the local table match is independently password-verified.

### Proof of Concept
Go integration test in `core/sessions/ldapauth` (or `core/web`):
1. Seed a local `users` table row: `email = "shared@example.com"`, `role = admin`, with a known local password hash.
2. Configure a fake/mock LDAP server (via the `LDAPClient` test double already used in `ldap_test.go`) so that `conn.Bind("uid=shared@example.com,...", "attacker-password")` succeeds, and the group search for `shared@example.com` returns zero matching groups (simulating a directory account with no RBAC group).
3. Call `ldapAuthenticator.CreateSession(ctx, sessions.SessionRequest{Email: "shared@example.com", Password: "attacker-password"})`.
4. Assert the call returns a valid session ID (no error) even though `attacker-password` does not match the local admin's `hashed_password`.
5. Query `ldap_sessions` for the created ID and assert `user_role == admin` and `localauth_user == false`, proving an externally-authenticated identity inherited the local admin role without password verification against that record — violating authentication-identity soundness.

### Citations

**File:** core/web/router.go (L214-217)
```go
	sc := NewSessionsController(app)
	unauth.POST("/sessions", sc.Create)
	auth := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	auth.DELETE("/sessions", sc.Destroy)
```

**File:** core/web/sessions_controller.go (L56-60)
```go
	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
		return
	}
```

**File:** core/sessions/ldapauth/ldap.go (L118-129)
```go
	// First check for the supported local admin users table
	var foundLocalAdminUser sessions.User
	checkErr := l.ds.GetContext(ctx, &foundLocalAdminUser, "SELECT * FROM users WHERE lower(email) = lower($1)", email)
	if checkErr == nil {
		return foundLocalAdminUser, nil
	}
	// If error is not nil, there was either an issue or no local users found
	if !errors.Is(checkErr, sql.ErrNoRows) {
		// If the error is not that no local user was found, log and exit
		l.lggr.Errorf("error searching users table: %v", checkErr)
		return sessions.User{}, errors.New("error Finding user")
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

**File:** core/sessions/ldapauth/ldap.go (L422-433)
```go
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
```

**File:** core/sessions/ldapauth/ldap.go (L440-452)
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
```
