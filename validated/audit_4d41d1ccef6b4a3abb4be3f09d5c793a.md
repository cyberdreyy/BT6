Based on my research, I found a concrete analog to the reported bug class (a security-critical value that should require strong, one-time or re-verified confirmation, but can instead be silently reset/added to by the current session holder without any additional proof of identity).

### Title
WebAuthn (2FA) credential enrollment lacks password re-verification, allowing session/token holders to silently add persistent authenticators - (File: core/web/webauthn_controller.go)

### Summary
`WebAuthnController.FinishRegistration` lets any already-authenticated request (session cookie or API token) register a brand-new WebAuthn/2FA credential for the current account with no re-verification of the account password, unlike the equivalent sensitive operation `NewAPIToken`, which explicitly requires `TestPassword` before minting a new credential.

### Finding Description
`WebAuthnController.BeginRegistration`/`FinishRegistration` are reachable via `POST /enroll_webauthn` and `GET /enroll_webauthn`, gated only by the generic `auth.Authenticate` middleware (session cookie or API token), with no extra role check and no password confirmation step: [1](#0-0) 

`FinishRegistration` obtains the current user purely from the request's session context, retrieves their existing WebAuthn credentials, verifies the browser attestation, then immediately persists a new credential via `AddCredentialToUser` → `SaveWebAuthn`, which is a pure `INSERT` with no uniqueness/limit constraint and no password check anywhere in the flow: [2](#0-1) [3](#0-2) 

Compare this to the analogous sensitive credential-issuance endpoint `NewAPIToken`, which requires the caller to prove knowledge of the current password (`TestPassword`) before a new API token is issued: [4](#0-3) 

This asymmetry means the "immutable"/high-trust security boundary (a registered second factor tied to an account) can be extended/reset repeatedly by anyone who merely holds a live session or API token — exactly the same class of defect as `FraxlendPair#setTimeLock`, where a value that should require a one-time, carefully-guarded change could instead be reset at will by any caller holding the relevant privilege, with no additional confirmation gate.

### Impact Explanation
If a session cookie or API token is compromised (e.g., via XSS, token leakage, an unattended browser, or SSRF that leaks the API token/cookie), the attacker can silently enroll their own physical/platform authenticator against the victim account. Even if the victim later rotates their password, the attacker's WebAuthn credential remains valid for login (per `AuthenticateUserByToken`/session-based login combined with WebAuthn as a possible second factor), giving the attacker persistent backdoor access to the Chainlink Node's admin UI/API — a durable account-takeover primitive established from what may have been only a transient credential leak.

### Likelihood Explanation
Any request that already carries a valid session cookie or API token for the target user can trigger this — no special role (admin/edit) is required, since the route is not wrapped by `RequiresAdminRole`/`RequiresEditRole`. Given how routinely session cookies/API tokens are exposed by lower-severity issues (XSS, log/leak, proxy misconfig), the reachability from an already-compromised-but-limited credential to a *persistent* backdoor is high.

### Recommendation
Require re-verification of the current password (or a similarly strong step-up, mirroring `NewAPIToken`'s `TestPassword` call) immediately before `FinishRegistration` persists a new WebAuthn credential. Additionally, consider auditing/notifying the user (email) whenever a new 2FA credential is added, and enforce a maximum number of concurrently registered credentials.

### Proof of Concept
1. Obtain a valid session cookie or API token for a victim account (e.g., via any secondary leak/XSS).
2. Using only that cookie/token, call `GET /v2/enroll_webauthn` to get registration options, then perform the WebAuthn ceremony with an attacker-controlled authenticator and `POST /v2/enroll_webauthn` with the resulting attestation.
3. Confirm via `wa.FinishRegistration` → `AddCredentialToUser` → `SaveWebAuthn` that a new credential row is inserted for the victim's email with no password prompt at any point in the request flow.
4. The attacker can now log in using the newly enrolled WebAuthn credential even after the leaked session/token is invalidated or the password is changed.

### Citations

**File:** core/web/router.go (L259-261)
```go
		wa := NewWebAuthnController(app)
		authv2.GET("/enroll_webauthn", wa.BeginRegistration)
		authv2.POST("/enroll_webauthn", wa.FinishRegistration)
```

**File:** core/web/webauthn_controller.go (L62-92)
```go
func (w *WebAuthnController) FinishRegistration(c *gin.Context) {
	ctx := c.Request.Context()
	user, ok := auth.GetAuthenticatedUser(c)
	if !ok {
		logger.Sugared(w.App.GetLogger()).AssumptionViolationf("failed to obtain current user from context")
		jsonAPIError(c, http.StatusInternalServerError, errors.New("unable to register key"))
		return
	}

	orm := w.App.AuthenticationProvider()
	uwas, err := orm.GetUserWebAuthn(ctx, user.Email)
	if err != nil {
		w.App.GetLogger().Errorf("failed to obtain current user MFA tokens: error in GetUserWebAuthn: %s", err)
		jsonAPIError(c, http.StatusInternalServerError, errors.New("unable to register key"))
		return
	}

	webAuthnConfig := w.App.GetWebAuthnConfiguration()

	credential, err := w.inProgressRegistrationsStore.FinishWebAuthnRegistration(*user, uwas, c.Request, webAuthnConfig)
	if err != nil {
		w.App.GetLogger().Errorf("error in FinishWebAuthnRegistration: %s", err)
		jsonAPIError(c, http.StatusBadRequest, errors.New("registration was unsuccessful"))
		return
	}

	if sessions.AddCredentialToUser(ctx, w.App.AuthenticationProvider(), user.Email, credential) != nil {
		w.App.GetLogger().Errorf("Could not save WebAuthn credential to DB for user: %s", user.Email)
		jsonAPIError(c, http.StatusInternalServerError, errors.New("internal Server Error"))
		return
	}
```

**File:** core/sessions/localauth/orm.go (L348-353)
```go
// SaveWebAuthn saves new WebAuthn token information.
func (o *orm) SaveWebAuthn(ctx context.Context, token *sessions.WebAuthn) error {
	sql := "INSERT INTO web_authns (email, public_key_data) VALUES ($1, $2)"
	_, err := o.ds.ExecContext(ctx, sql, token.Email, token.PublicKeyData)
	return err
}
```

**File:** core/web/user_controller.go (L266-282)
```go
	}
	// In order to create an API token, login validation with provided password must succeed
	err = u.App.AuthenticationProvider().TestPassword(ctx, sessionUser.Email, request.Password)
	if err != nil {
		u.App.GetAuditLogger().Audit(audit.APITokenCreateAttemptPasswordMismatch, map[string]any{"user": user.Email})
		jsonAPIError(c, http.StatusUnauthorized, errors.New("incorrect password"))
		return
	}
	newToken := auth.NewToken()
	if err := u.App.AuthenticationProvider().SetAuthToken(ctx, &user, newToken); err != nil {
		if errors.Is(err, clsession.ErrNotSupported) {
			jsonAPIError(c, http.StatusBadRequest, errUnsupportedForAuth)
			return
		}
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}
```
