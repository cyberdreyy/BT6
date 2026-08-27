### Title
Potential Authentication Bypass via Empty API Token Matching a Revoked User Token - ([File: core/sessions/localauth/orm.go])

### Summary
This is analogous to the "failure to validate initialization" bug class from the report: an uninitialized/cleared value (empty string) can be retrieved from a data store and treated as a valid credential, because the lookup does not check whether the field was actually initialized/assigned before accepting it as a match.

### Finding Description
`FindUserByAPIToken` performs a direct equality lookup of the caller-supplied token against the `users.token_key` column with no guard for empty/uninitialized values: [1](#0-0) 

When a user's API token is revoked, `DeleteAuthToken` does not `NULL` the column or delete the row — it explicitly sets `token_key`, `token_salt`, and `token_hashed_secret` to the empty string `''`: [2](#0-1) 

This mirrors the `finalize_locked_stake` bug class: a "reset"/uninitialized value (`''`) is left in a retrievable, queryable state rather than being distinguished from a genuinely-set token. If the HTTP layer that extracts the API token header (`AuthenticateByToken` in `core/web/auth/auth.go`, referenced via the `Authenticator` interface's `FindUserByAPIToken`) does not explicitly reject an empty/missing token value before calling `FindUserByAPIToken`, an unauthenticated client sending a request with an empty (or absent, if `c.GetHeader` returns `""`) API-token header would match `WHERE token_key = ''` and be authenticated as any user whose API token has been revoked via `DeleteAuthToken`. [3](#0-2) 

I was not able to retrieve the exact body of `AuthenticateByToken` (approximately lines 73–111 of `core/web/auth/auth.go`) in this session, so I cannot confirm whether it already validates for a non-empty token before invoking `FindUserByAPIToken`. This is the key unverified step required to fully confirm exploitability.

### Impact Explanation
If the empty-token guard is missing, this would allow an unauthenticated attacker to impersonate any user account that has had its API token revoked (a common admin action, e.g. during offboarding or key rotation), gaining that user's full role (potentially Admin), i.e. a concrete authentication/role bypass.

### Likelihood Explanation
Likelihood depends entirely on the unverified header-extraction/validation logic in `AuthenticateByToken`. If that function already checks `apiToken != ""` before querying, this finding is not exploitable. Because chainlink typically hashes tokens and validates access-key/secret pairs (as seen in the analogous `bridges.AuthenticateExternalInitiator`), it is plausible protective checks exist, but this could not be confirmed with the available tooling.

### Recommendation
- Reject empty/missing API token values in `AuthenticateByToken` before calling `FindUserByAPIToken`.
- In `FindUserByAPIToken`, explicitly treat empty `token_key` as "no token configured" (e.g., `WHERE token_key = $1 AND token_key != ''`) rather than relying on the caller to never send an empty value.
- Consider setting `token_key`/`token_salt`/`token_hashed_secret` to `NULL` in `DeleteAuthToken` instead of empty string, so an empty/blank credential can never match a row via a simple equality query.

### Proof of Concept
Not fully verifiable without confirming the `AuthenticateByToken` implementation. Conceptually: (1) create a user, set then revoke their API token via `DeleteAuthToken` (setting `token_key=''`); (2) send an authenticated API request with an empty/blank API-token header; (3) if `AuthenticateByToken` forwards this empty value to `FindUserByAPIToken`, the query `SELECT * FROM users WHERE token_key = ''` returns the revoked user, granting access under their role.

### Citations

**File:** core/sessions/localauth/orm.go (L48-53)
```go
// FindUserByAPIToken will attempt to return an API user via the user's table token_key column.
func (o *orm) FindUserByAPIToken(ctx context.Context, apiToken string) (user sessions.User, err error) {
	sql := "SELECT * FROM users WHERE token_key = $1"
	err = o.ds.GetContext(ctx, &user, sql, apiToken)
	return
}
```

**File:** core/sessions/localauth/orm.go (L343-346)
```go
func (o *orm) DeleteAuthToken(ctx context.Context, user *sessions.User) error {
	sql := "UPDATE users SET token_salt = '', token_key = '', token_hashed_secret = '', updated_at = now() WHERE email = $1 RETURNING *"
	return o.ds.GetContext(ctx, user, sql, user.Email)
}
```

**File:** core/web/auth/auth.go (L40-45)
```go
type Authenticator interface {
	AuthorizedUserWithSession(ctx context.Context, sessionID string) (clsessions.User, error)
	FindExternalInitiator(ctx context.Context, eia *auth.Token) (*bridges.ExternalInitiator, error)
	FindUser(ctx context.Context, email string) (clsessions.User, error)
	FindUserByAPIToken(ctx context.Context, apiToken string) (clsessions.User, error)
}
```
