### Title
Missing `return` after failed OIDC email-claim/session-creation checks allows an authenticated session to be created and returned despite the failure - (File: `core/sessions/oidcauth/oidc.go`)

### Summary
`handleTokenExchange` in `core/sessions/oidcauth/oidc.go` is the internet-facing OIDC callback handler that exchanges an authorization code for tokens and creates an authenticated Chainlink session. Two of its error branches write an HTTP error response but omit the `return` statement, so execution falls through and completes the "success" path anyway — creating and persisting a valid session cookie and returning `200 OK` even though the preceding check failed. This is the same root-cause pattern as the reported `SEuroOffering.transferCollateral()` bug: a conditional that is supposed to gate/halt further processing (functionally a `require`) is implemented as a non-halting `if`, so the failure condition is silently bypassed and the "happy path" continues to run with corrupted/incomplete state.

### Finding Description
In `handleTokenExchange` [1](#0-0) :

1. Email-claim extraction check (missing `return`): [2](#0-1) 
When the verified ID token's claims do not contain a string `email` field, the code logs an error and writes `http.StatusInternalServerError` to the response, but does **not** `return`. Execution continues straight into role mapping and session creation using an empty `email` string.

2. Session-insert-failure check (missing `return`): [3](#0-2) 
If the `INSERT INTO oidc_sessions ...` call fails, an error is logged and `http.StatusInternalServerError` is written to the response — again without `return`. Execution falls through to setting the session cookie (`ginSession.Set(...)`, `ginSession.Save()`) and ultimately returns `http.StatusOK` with `Success: true` at the very end of the function: [4](#0-3) 

Both are structurally identical to the SEuroOffering pattern: a check that should hard-stop request processing (`require`) is coded as a soft `if` with only logging/response-writing side effects, and the caller relies on the subsequent code path never running for the failure case, but nothing actually prevents it. In Gin, calling `c.String`/`c.JSON` again later in the same handler doesn't invalidate the earlier write; only the first `WriteHeader` call sets the actual status code sent to the client (later writes just append body bytes and log a "superfluous response.WriteHeader call" warning), so the client typically still receives the final `200 OK` JSON body and, more importantly, a valid `Set-Cookie` session header, because `ginSession.Save()` executed unconditionally after these faulty checks.

### Impact Explanation
This is a request-handling correctness/authorization-adjacent bug in the internet-facing sign-in callback endpoint:
- In case 1, when the identity provider omits/doesn't populate the `email` claim (e.g., no `email` scope granted, or provider quirk), the handler still maps the OIDC group claims to an RBAC role (`admin`/`edit`/`run`/`read`) and issues a fully valid, cookie-backed authenticated session — but registers it in `oidc_sessions` under an empty email. Any two OIDC principals hitting this path collide on the same "empty" identity record, which is a cross-identity confusion in the session store and undermines per-user session tracking/audit trust for the roles granted.
- In case 2, if the DB insert itself fails, the code still proceeds to set and save the session cookie referencing a `clSession.ID` that was never persisted to `oidc_sessions`, returning a false "success" to the client. This produces an inconsistent state (client believes it's logged in) and masks a genuine backend failure that should have blocked authentication.

Overall, this weakens the guarantee that a `200`/cookie response corresponds to a correctly-provisioned, uniquely identified backend session — a control-flow class of bug directly analogous to the reported Solidity issue where a failed condition is not enforced and lets the "transfer"/"session-issuance" happen anyway.

### Likelihood Explanation
Both branches are reachable by any unprivileged external client that can complete (or partially complete) the standard OIDC authorization-code flow against `/oidc/tokenExchange` (`handleTokenExchange`), which requires only a valid `code`/`state` and a token that verifies against the configured provider's JWKS — it does not require any pre-existing Chainlink privilege. The email-omission case is directly triggerable by controlling what the identity provider (or a malicious/compromised OIDC provider) returns in the ID token, since Chainlink does not enforce that `email` be present before proceeding. The DB-insert-failure case requires a backend fault (e.g., transient DB error) to trigger, making it lower likelihood but still a data-integrity/availability inconsistency once it occurs.

### Recommendation
Add `return` immediately after each error-response write in `handleTokenExchange`, matching the "replace `if` (non-halting) by `require` (halting)" fix pattern from the original report:
```go
email, ok := claims["email"].(string)
if !ok {
    oi.lggr.Errorf("Failed to get email from claims. error: %v", err)
    c.String(http.StatusInternalServerError, "Failed to get email from claims")
    return
}
...
if err != nil {
    oi.lggr.Errorf("unable to create new session in oidc_sessions table %v", err)
    c.String(http.StatusInternalServerError, "Error creating session")
    return
}
```
Additionally, consider aborting the gin context (`c.Abort()`) as defense-in-depth so any accidental later handler code cannot execute after an error response has been written.

### Proof of Concept
1. Configure the node with OIDC auth enabled (`OIDCAuth`), with valid `AdminClaim`/`EditClaim`/`RunClaim`/`ReadClaim` group mappings.
2. Use (or stand up) an OIDC provider/token whose ID token includes the configured group claim (so `IDClaimsToUserRole` succeeds) but does not include an `email` claim, or where `email` is present as a non-string type.
3. Complete the normal `/oidc/signIn` → provider → `/oidc/tokenExchange` flow with `code`/`state` from that provider.
4. Observe that despite hitting the `!ok` branch in the email check at [5](#0-4) , the handler still calls `IDClaimsToUserRole`, inserts a row in `oidc_sessions` with `user_email = ''`, sets `ginSession` with a valid session ID, calls `ginSession.Save()`, and finally responds `200 OK` with `{"success": true}` (in addition to/instead of the earlier `500` body written), yielding a valid authenticated cookie session for an identity with no email on record.

### Citations

**File:** core/sessions/oidcauth/oidc.go (L163-163)
```go
func (oi *oidcAuthenticator) handleTokenExchange(c *gin.Context) {
```

**File:** core/sessions/oidcauth/oidc.go (L226-231)
```go
	email, ok := claims["email"].(string)
	if !ok {
		oi.lggr.Errorf("Failed to get email from claims. error: %v", err)
		c.String(http.StatusInternalServerError, "Failed to get email from claims")
	}
	oi.lggr.Tracef("Received and validated ID claims: %v\n", idClaims)
```

**File:** core/sessions/oidcauth/oidc.go (L247-260)
```go
	// Save new user authenticated clSession and role to oidc_sessions table
	// Sessions are set to expire after the duration + creation date elapsed
	clSession := clsessions.NewSession()
	_, err = oi.ds.ExecContext(
		ctx,
		"INSERT INTO oidc_sessions (id, user_email, user_role, created_at) VALUES ($1, $2, $3, now())",
		clSession.ID,
		strings.ToLower(email),
		role,
	)
	if err != nil {
		oi.lggr.Errorf("unable to create new session in oidc_sessions table %v", err)
		c.String(http.StatusInternalServerError, "Error creating session")
	}
```

**File:** core/sessions/oidcauth/oidc.go (L264-276)
```go
	// save session
	ginSession.Set(webauth.SessionIDKey, clSession.ID)
	err = ginSession.Save()
	if err != nil {
		oi.lggr.Errorf("failed to saved session %v", err)
		c.String(http.StatusInternalServerError, "Authentication failed")
		return
	}

	c.JSON(http.StatusOK, ExchangeTokenResponse{
		Success: true,
	})
}
```
