### Title
User enumeration of local admin accounts via distinguishable error messages returned by `localLoginFallback` - ([File: core/sessions/ldapauth/ldap.go])

### Summary
`localLoginFallback` returns the raw `sql.ErrNoRows` error unmodified when a local admin email does not exist, but returns distinct custom errors (`"invalid email"` / `"invalid password"`) when the email exists. These raw error values are propagated unmodified all the way to the HTTP client, letting an unauthenticated caller distinguish "email not registered as local admin" from "email registered, wrong password."

### Finding Description
`CreateSession` (core/sessions/ldapauth/ldap.go:396) falls back to `localLoginFallback` (core/sessions/ldapauth/ldap.go:624) whenever the LDAP bind/FindUser path fails. `localLoginFallback` does:
```go
err := l.ds.GetContext(ctx, &user, sql, sr.Email)
if err != nil {
    return user, err   // raw sql.ErrNoRows surfaces for non-existent local emails
}
if !constantTimeEmailCompare(...) { return user, errors.New("invalid email") }
if !utils.CheckPasswordHash(...) { return user, errors.New("invalid password") }
``` [1](#0-0) 

- Non-existent local admin email → `err == sql.ErrNoRows` (message: `"sql: no rows in result set"`), returned immediately with no bcrypt work performed.
- Existent local admin email + wrong password → distinct message `"invalid password"`, only reached after a bcrypt comparison (`utils.CheckPasswordHash`) has run.

This error is passed straight up through `CreateSession` (`returnErr`) at core/sessions/ldapauth/ldap.go:431 to `SessionsController.Create`, which forwards it verbatim to the client:
```go
sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
if err != nil {
    jsonAPIError(c, http.StatusUnauthorized, err)   // raw err.Error() exposed
    return
}
``` [2](#0-1) 

No middleware or presenter sits between `CreateSession`'s returned error and the HTTP response body, so the distinct error text (and the timing difference caused by skipping bcrypt for non-existent emails) is directly observable by an unauthenticated caller of `POST /sessions`.

The `constantTimeEmailCompare` call at core/sessions/ldapauth/ldap.go:631 is effectively dead/redundant: the preceding SQL already filters by `lower(email) = lower($1)`, so any row returned already matches case-insensitively; the "case-manipulation password bypass" theorized in the question does not exist — that check can never fail for a returned row, so it does not create an additional oracle beyond the row-found/row-not-found and password-mismatch error already noted.

### Impact Explanation
This is a **user/credential enumeration** vulnerability (matches Chainlink bounty class "information disclosure enabling further attack," specifically an authentication oracle). An attacker can determine which email addresses have local CLI admin accounts on a node without any credentials, by observing whether the response is `"sql: no rows in result set"` vs `"invalid password"`/`"invalid email"`, and by observing the timing difference (bcrypt is comparatively slow). This narrows targeted credential-stuffing/password-guessing/social-engineering attacks against known-valid local admin accounts, which have full admin privileges (job creation/deletion, key management, fund movement via bridges). It does not by itself bypass authentication or disclose secrets/passwords.

### Likelihood Explanation
No preconditions beyond LDAP authentication being configured with the local-admin fallback enabled (`localLoginFallback` is only reached when LDAP auth is active). The attacker needs no privileges — just network access to `POST /sessions`, matching the "unauthenticated CreateSession caller" precondition. The attack is trivially repeatable (send two requests, compare `errors` field / response body and/or latency).

### Recommendation
- In `localLoginFallback`, normalize all failure branches (no-row, wrong email, wrong password) to the exact same generic error (e.g., `sessions.ErrInvalidCredentials` or a single `"invalid credentials"` message) before returning to `CreateSession`.
- Perform a dummy/constant bcrypt comparison against a fixed placeholder hash when the user is not found, so total execution time doesn't differ between existing and non-existing emails.
- Remove the now-redundant `constantTimeEmailCompare` call or keep it only as defense-in-depth, but ensure it does not change externally observable behavior.
- Ensure `SessionsController.Create` returns a single generic error message/status for all authentication failures rather than forwarding the underlying provider error verbatim.

### Proof of Concept
Go table test for `localLoginFallback` (and/or handler-level test hitting `POST /sessions`):
1. Seed `users` table with one local admin `existing@example.com` / known password hash.
2. Case A: call `localLoginFallback` (or `POST /sessions`) with `email=nonexistent@example.com`, wrong password. Assert error is `sql.ErrNoRows` (or, at handler level, response body contains `"sql: no rows in result set"`).
3. Case B: call with `email=existing@example.com`, wrong password. Assert error is `"invalid password"` (handler-level response body differs from Case A).
4. Assert the two error strings/types differ — proving an enumeration oracle exists.
5. Optionally measure wall-clock time for both calls over N iterations and assert Case B is measurably slower on average due to bcrypt execution, corroborating the timing side channel.
6. Fix verification: after normalizing errors, re-run steps 2–4 and assert both cases yield an identical opaque error string.

### Citations

**File:** core/sessions/ldapauth/ldap.go (L624-642)
```go
func (l *ldapAuthenticator) localLoginFallback(ctx context.Context, sr sessions.SessionRequest) (sessions.User, error) {
	var user sessions.User
	sql := "SELECT * FROM users WHERE lower(email) = lower($1)"
	err := l.ds.GetContext(ctx, &user, sql, sr.Email)
	if err != nil {
		return user, err
	}
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		l.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return user, errors.New("invalid email")
	}

	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		l.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return user, errors.New("invalid password")
	}

	return user, nil
}
```

**File:** core/web/sessions_controller.go (L56-60)
```go
	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
		return
	}
```
