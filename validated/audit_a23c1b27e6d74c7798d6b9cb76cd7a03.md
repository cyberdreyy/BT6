### No Vulnerability found for this question.

**Rationale:** `AuthenticateExternalInitiator` in `core/web/auth/auth.go` performs a constant-time comparison of a hashed secret against the stored `HashedSecret` [1](#0-0) , and the underlying comparison in `bridges.AuthenticateExternalInitiator` uses `subtle.ConstantTimeCompare` against a salted hash with no nonce/timestamp field [2](#0-1) . This is a static bearer-credential model, identical in kind to the standard `AuthenticateByToken` (`X-API-KEY`/`X-API-SECRET`) authentication used elsewhere in the same file [3](#0-2) , and to session cookies — none of which implement nonce/timestamp replay windows either. This is a known, industry-standard bearer-credential design (comparable to API access-key/secret pairs), not a defect unique to this function, and revocation is possible by deleting/rotating the external initiator record.

The stated preconditions for exploitation — "compromised log line" (host/operator-level issue) or "MITM before TLS termination" (network-layer attack) — are both explicitly excluded by the rules ("Reject ... network-layer, host-level, operator-only, and misconfiguration-only paths"). Without TLS compromise or host-level log leakage, there is no attacker-reachable path from an unprivileged actor to capture the secret in the first place, so this does not meet the bar of a concrete, reachable privilege escalation from an unprivileged network client. This is a design-level lack of replay protection common to bearer-token schemes, not an exploitable authentication bypass reachable by the defined unprivileged attacker.

### Citations

**File:** core/web/auth/auth.go (L78-112)
```go
func AuthenticateByToken(c *gin.Context, authr Authenticator) error {
	ctx := c.Request.Context()
	token := &auth.Token{
		AccessKey: c.GetHeader(APIKey),
		Secret:    c.GetHeader(APISecret),
	}
	if token.AccessKey == "" {
		return auth.ErrorAuthFailed
	}

	if token.Secret == "" {
		return auth.ErrorAuthFailed
	}

	// We need to first load the user row so we can compare tokens using the stored salt
	user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
			return auth.ErrorAuthFailed
		}
		return err
	}

	ok, err := clsessions.AuthenticateUserByToken(token, &user)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

	c.Set(SessionUserKey, &user)

	return nil
}
```

**File:** core/web/auth/auth.go (L119-141)
```go
func AuthenticateExternalInitiator(c *gin.Context, store Authenticator) error {
	ctx := c.Request.Context()
	eia := &auth.Token{
		AccessKey: c.GetHeader(static.ExternalInitiatorAccessKeyHeader),
		Secret:    c.GetHeader(static.ExternalInitiatorSecretHeader),
	}

	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
	}

	ok, err := bridges.AuthenticateExternalInitiator(eia, ei)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}
```

**File:** core/bridges/external_initiator.go (L59-67)
```go
// AuthenticateExternalInitiator compares an auth against an initiator and
// returns true if the password hashes match
func AuthenticateExternalInitiator(eia *auth.Token, ea *ExternalInitiator) (bool, error) {
	hashedSecret, err := auth.HashedSecret(eia, ea.Salt)
	if err != nil {
		return false, err
	}
	return subtle.ConstantTimeCompare([]byte(hashedSecret), []byte(ea.HashedSecret)) == 1, nil
}
```
