### Title
Local `users` table lookup in `FindUser` takes precedence over LDAP group membership, letting a valid low-privilege LDAP user assume a stale local-admin role via `CreateSession` - ([File: core/sessions/ldapauth/ldap.go])

### Summary
`(l *ldapAuthenticator).CreateSession` calls `FindUser` unconditionally after a successful LDAP `Bind`, and `FindUser` checks the local `users` table for a matching email *before* ever consulting LDAP group membership. If a stale/duplicate row for the attacker's email exists in the local `users` table with role `admin`, a correctly-authenticated LDAP user whose real LDAP group is only `Read` will be issued an `ldap_sessions` entry with the admin role, with no local password verification at all.

### Finding Description
In `CreateSession` (`core/sessions/ldapauth/ldap.go:396-433`), the flow is:
1. `conn.Bind(searchBaseDN, sr.Password)` — attacker binds with their own valid LDAP credentials (member of the `Read` group only). Bind succeeds, so `returnErr` stays `nil`. [1](#0-0) 
2. `foundUser, err := l.FindUser(ctx, escapedEmail)` is then called **unconditionally**, regardless of Bind's outcome. [2](#0-1) 
3. Inside `FindUser`, the *first* thing checked is the local `users` table by email — not LDAP group membership: [3](#0-2) 
If a row exists (e.g. a stale/duplicate local-admin fixture sharing the attacker's email), `FindUser` returns that row's role (`admin`) with `err == nil`, and never reaches the LDAP group-search logic (`groupSearchResultsToUserRole`) that would have correctly assigned the `Read`/`view` role.
4. Because `err == nil`, `returnErr` is never set, so `localLoginFallback` (which would validate `sr.Password` against the local user's `hashed_password`) is skipped entirely: [4](#0-3) 
5. The session is then persisted using `foundUser.Role` (admin, from the local table) and returned to the caller. [5](#0-4) 

The attacker only needs to know their own valid LDAP credentials (a legitimate `Read`-only account); they never need to know the local admin's password, because the local-table role is adopted without any password check against that row.

### Impact Explanation
This is a privilege escalation from LDAP-verified `Read`/`Run` role to `Admin`, matching Chainlink's "authorization/role bypass leading to node compromise" bounty class. The escalated session's role is persisted into `ldap_sessions` and used for all subsequent RBAC decisions via `AuthorizedUserWithSession`, so the attacker obtains a fully functional admin session cookie/ID without ever holding admin credentials.

### Likelihood Explanation
Requires the precondition explicitly named in the question: a stale/duplicate row in the local `users` table sharing the attacker's email with a higher role (e.g., left over from initial CLI bootstrap admin setup, a common real-world Chainlink node operational pattern per `docs`/CLI admin creation flow referenced in `core/sessions/authentication.go`'s `BasicAdminUsersORM` comment). Given that precondition, exploitation is trivial and fully repeatable: any legitimate LDAP login attempt for that email will always resolve through `FindUser`'s local-table short-circuit. [6](#0-5) 

### Recommendation
In `FindUser`, only consult the local `users` table as a fallback for emails that are absent from the LDAP directory (as already done later, at `ldap.go:173-188`), not as the first-priority check. The current ordering (local-table lookup before LDAP group resolution at `ldap.go:118-129`) should be removed or reordered so that any email present in the LDAP directory is always resolved via LDAP group membership, and the local `users` table is consulted only through the explicit `localLoginFallback` path (which enforces password verification) or for emails LDAP has no record of at all.

### Proof of Concept
Go test plan (extending `core/sessions/ldapauth/ldap_test.go`):
1. Seed the local `users` table with a row `email = "shared@example.com"`, `role = admin` (simulating a stale/duplicate fixture), using its own distinct password hash.
2. Configure `mockLdapClient`/`mockLdapConnProvider` so that:
   - `Bind` succeeds for `shared@example.com` with an attacker-known LDAP password.
   - The subsequent LDAP group search returns membership only in the `Read` group CN.
3. Call `ldapAuthProvider.CreateSession(ctx, sessions.SessionRequest{Email: "shared@example.com", Password: "<attacker LDAP password>"})`.
4. Assert `err == nil` (session created) and query `ldap_sessions` for `user_role` — assert it currently comes back `admin` (proving the bug), whereas the expected/fixed behavior should be `view` (matching the LDAP `Read` group) and/or the login should be rejected without correct local password verification.

### Citations

**File:** core/sessions/ldapauth/ldap.go (L115-123)
```go
func (l *ldapAuthenticator) FindUser(ctx context.Context, email string) (sessions.User, error) {
	email = strings.ToLower(email)

	// First check for the supported local admin users table
	var foundLocalAdminUser sessions.User
	checkErr := l.ds.GetContext(ctx, &foundLocalAdminUser, "SELECT * FROM users WHERE lower(email) = lower($1)", email)
	if checkErr == nil {
		return foundLocalAdminUser, nil
	}
```

**File:** core/sessions/ldapauth/ldap.go (L406-411)
```go
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

**File:** core/sessions/authentication.go (L32-41)
```go
// BasicAdminUsersORM is the interface that defines the functionality required for supporting basic admin functionality
// adjacent to the identity provider authentication provider implementation. It is currently implemented by the local
// users/sessions ORM containing local admin CLI actions. This is separate from the AuthenticationProvider,
// as local admin management (ie initial core node setup, initial admin user creation), is always
// required no matter what the pluggable AuthenticationProvider implementation is.
type BasicAdminUsersORM interface {
	ListUsers(ctx context.Context) ([]User, error)
	CreateUser(ctx context.Context, user *User) error
	FindUser(ctx context.Context, email string) (User, error)
}
```
