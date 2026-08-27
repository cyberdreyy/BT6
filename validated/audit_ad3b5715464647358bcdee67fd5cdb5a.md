### Title
Pre-authentication WebAuthn/MFA lookup in `SessionsController.Create` reintroduces the MFA-enrollment timing/oracle leak that `orm.CreateSession` was explicitly designed to prevent - ([File: core/web/sessions_controller.go])

### Summary
`SessionsController.Create` unconditionally calls `GetUserWebAuthn(ctx, sr.Email)` for the attacker-supplied email *before* any password verification takes place, regardless of whether the password will turn out to be correct. This defeats the deliberate ordering protection documented and implemented in `localauth/orm.go`'s `CreateSession`, whose comment explicitly states password/email checks are done first "to prevent extra database look up for MFA tokens leaking if an account has MFA tokens or not."

### Finding Description
The unauthenticated route `POST /sessions` is wired to `SessionsController.Create` with no auth middleware: [1](#0-0) .

In `Create`, the attacker-controlled `sr.Email` from the JSON body is used to query `GetUserWebAuthn` immediately after binding, before `AuthenticationProvider().CreateSession` (which performs password verification) is ever invoked: [2](#0-1) 

Contrast this with the local ORM's own `CreateSession`, which was specifically written to look up email/password *first*, and only load WebAuthn/MFA state *after* password validation succeeds — precisely to avoid an extra, MFA-status-correlated database lookup for wrong-password or wrong-email attempts: [3](#0-2) 

Because the controller performs its own `GetUserWebAuthn` call unconditionally — for every request, independent of whether the eventual `CreateSession` call will fail on "Invalid email" or "Invalid password" — the protective ordering inside `orm.CreateSession` is bypassed at the HTTP-handler layer. The controller-level call happens for any email an unauthenticated caller supplies (valid or invalid, later found in `orm.CreateSession` or not), performing an additional DB round trip whose cost/row-count is correlated with whether that account exists and whether it has enrolled WebAuthn credentials (`SELECT ... FROM web_authns WHERE LOWER(email) = $1`): [4](#0-3) .

This creates a pre-authentication side channel: attackers can probe arbitrary emails and, through response timing on the always-executed `GetUserWebAuthn` query (row count 0 for no-MFA/non-existent users vs. >0 for MFA-enrolled users, plus the conditional extra work of setting `sr.SessionStore`/`sr.WebAuthnConfig`), infer MFA-enrollment status without ever needing a correct password. This is the exact invariant the ORM-level fix comment says must be preserved but which the controller does not honor, since it performs its own independent lookup ahead of any credential check.

### Impact Explanation
This is an information-disclosure issue enabling MFA-enrollment reconnaissance pre-authentication: an unauthenticated attacker can determine whether a targeted account has WebAuthn/MFA enabled, which is directly useful for prioritizing account-takeover/credential-stuffing targets (MFA-disabled accounts) or crafting targeted WebAuthn-relay/phishing attacks against MFA-enabled ones. It maps to Chainlink's "information disclosure" bounty class rather than direct authentication bypass, since no session or credential is obtained directly.

### Likelihood Explanation
No privilege is required — this is reachable by any unauthenticated caller of `POST /sessions` with only rate-limiting applied: [5](#0-4) . The extra query executes on every single request, so the oracle is trivially repeatable at the rate limit's ceiling. The practical exploitability depends on the measurable timing/row-count difference being statistically distinguishable over the network, which is feasible for a determined, patient attacker (classic timing side-channel), especially combined with the fact this is a real logic deviation from the documented anti-leak design, not just generic network jitter.

### Recommendation
Remove the unconditional `GetUserWebAuthn` call from `SessionsController.Create`. Let `AuthenticationProvider().CreateSession` be the single source of truth for password verification and MFA lookup ordering (as already implemented in `orm.CreateSession`), and have the controller pass the `WebAuthnSessionStore`/`WebAuthnConfiguration` into every `CreateSession` call unconditionally (or lazily resolve them only after password validation succeeds inside the provider), rather than pre-querying WebAuthn status keyed by attacker-supplied email prior to authentication.

### Proof of Concept
Go handler-level test plan (`core/web/sessions_controller_test.go`):
1. Create two users via `AuthenticationProvider().CreateUser`: `userNoMFA` (no WebAuthn credential) and `userMFA` (register a WebAuthn credential via `sessions.AddCredentialToUser`, mirroring `TestORM_WebAuthn` setup: [6](#0-5) ).
2. Send `POST /sessions` with **incorrect password** for `userNoMFA` and separately for `userMFA`; assert both return `401` with identical error body (`"Invalid password"`), confirming no *direct* body-based leak — this isolates the issue to the additional pre-auth query itself.
3. Using a mock/spy `AuthenticationProvider` (e.g. `core/sessions/mocks/authentication_provider.go`), assert that `GetUserWebAuthn` is invoked with the attacker's `sr.Email` even when the subsequent `CreateSession` call is going to fail due to bad credentials — proving the call happens unconditionally pre-authentication, which is the root-cause code defect.
4. Optionally, run repeated timed requests (N ≥ 1000 per case) against `userMFA` vs `userNoMFA` with wrong passwords and compare mean/percentile latency of the `GetUserWebAuthn`-driven request path to demonstrate a statistically significant differential attributable to the extra WebAuthn row lookup.

### Citations

**File:** core/web/router.go (L207-216)
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
```

**File:** core/web/sessions_controller.go (L34-56)
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
```

**File:** core/sessions/localauth/orm.go (L130-139)
```go
func (o *orm) GetUserWebAuthn(ctx context.Context, email string) ([]sessions.WebAuthn, error) {
	var uwas []sessions.WebAuthn
	err := o.ds.SelectContext(ctx, &uwas, "SELECT email, public_key_data FROM web_authns WHERE LOWER(email) = $1", strings.ToLower(email))
	if err != nil {
		return uwas, err
	}
	// In the event of not found, there is no MFA on this account and it is not an error
	// so this returns either an empty list or list of WebAuthn rows
	return uwas, nil
}
```

**File:** core/sessions/localauth/orm.go (L144-170)
```go
func (o *orm) CreateSession(ctx context.Context, sr sessions.SessionRequest) (string, error) {
	user, err := o.FindUser(ctx, sr.Email)
	if err != nil {
		return "", err
	}
	lggr := o.lggr.With("user", user.Email)
	lggr.Debugw("Found user")

	// Do email and password check first to prevent extra database look up
	// for MFA tokens leaking if an account has MFA tokens or not.
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		o.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid email")
	}

	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}

	// Load all valid MFA tokens associated with user's email
	uwas, err := o.GetUserWebAuthn(ctx, user.Email)
	if err != nil {
		// There was an error with the database query
		lggr.Errorf("Could not fetch user's MFA data: %v", err)
		return "", pkgerrors.New("MFA Error")
	}
```

**File:** core/sessions/localauth/orm_test.go (L253-259)
```go
	cred := webauthn.Credential{
		ID:              []byte("test-id"),
		PublicKey:       []byte("test-key"),
		AttestationType: "test-attestation",
	}
	require.NoError(t, sessions.AddCredentialToUser(ctx, orm, initial.Email, &cred))

```
