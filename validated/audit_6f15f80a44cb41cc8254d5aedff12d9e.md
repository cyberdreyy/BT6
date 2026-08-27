### Title
Sensitive key-export endpoints lack step-up re-authentication, unlike other sensitive account actions - ([File: core/web/keys_controller.go])

### Summary
This is a valid analog of the "keychain security level is not the strongest available" bug class. The iOS report's root complaint is that sensitive credential operations (fetching/decrypting the mnemonic) are gated only by baseline access control, with no requirement to re-prove user presence/identity immediately before the sensitive action. Chainlink's node API has the same structural weakness for private-key export: any request holding a valid Admin-role session cookie or API token can immediately export the raw (re-encrypted) private key material for ETH, OCR, OCR2, P2P, CSA, and other key types, with no re-verification of the account password or WebAuthn/MFA, even though the codebase already implements exactly that kind of step-up check for a comparable sensitive action (API token creation).

### Finding Description
Key export routes are wired only with `auth.RequiresAdminRole`, itself built on top of `auth.Authenticate` with `AuthenticateByToken`/`AuthenticateBySession`: [1](#0-0) [2](#0-1) 

`RequiresAdminRole` only checks the role attached to the already-authenticated session/token — it performs no additional credential check: [3](#0-2) 

The generic `keysController.Export` (used for OCR/OCR2/P2P/CSA/Solana/Cosmos/StarkNet/etc.) and `ETHKeysController.Export` decrypt/re-encrypt the private key using only a caller-supplied `newpassword` query parameter, with no verification of the requesting user's own account password or MFA token: [4](#0-3) [5](#0-4) 

By contrast, the node already implements this "user presence" style re-authentication for another admin-triggerable, high-impact action — issuing a new API token — where `TestPassword` must succeed against the currently logged-in user's actual account password before the sensitive action proceeds: [6](#0-5) 

Similarly, WebAuthn/MFA enrollment is only enforced at login time (`CreateSession`), not before this key-export action, even for accounts that have MFA enabled: [7](#0-6) 

The inconsistency shows the export endpoints were not designed with the same "prove presence again before touching key material" principle that the codebase applies elsewhere, mirroring the keychain report's core complaint: the strongest security tier (user-presence re-check) is available/implemented in the codebase pattern but not applied to the more sensitive private-key operation.

### Impact Explanation
If an attacker obtains a valid Admin-role session cookie or API token (e.g., via XSS, token leakage, session fixation, or a compromised CI credential), they can immediately export the encrypted JSON key material for the node's ETH, OCR2, P2P, and CSA keys without needing to know the account's actual login password or pass MFA again, and can pick their own `newpassword` for the exported blob. This gives a foothold to eventually derive the private key and sign/relay transactions or forge OCR/P2P messages, exactly the "unauthorized user is able to sign transactions" exploit scenario described in the report.

### Likelihood Explanation
The likelihood depends on an attacker first obtaining a valid Admin-scoped session or API token — this is not achievable by a fully unauthenticated actor. However, once any such credential is obtained (a scenario Chainlink's own audit logging and step-up-auth pattern for `NewAPIToken` already anticipates as worth defending against), exploitation of the export endpoint is trivial and requires no additional secret.

### Recommendation
Require re-verification of the current user's account password (and WebAuthn/MFA if enrolled) immediately before serving `Export` for any key type, consistent with the pattern already used in `UserController.NewAPIToken`. Consider also requiring a short-lived, freshly-issued "step-up" token for these operations rather than relying solely on the ambient session/API token used for general API access.

### Proof of Concept
1. Obtain (e.g., via phishing/XSS/leak) a valid Admin-role session cookie or API token for a running Chainlink node.
2. Send `POST /v2/keys/eth/export/{address}?newpassword=attackerchoice` (or the equivalent `/v2/keys/ocr2/export/:ID`, `/v2/keys/p2p/export/:ID`, `/v2/keys/csa/export/:ID`) using only that session/token — no password or MFA challenge is required.
3. Receive the encrypted key JSON re-encrypted with the attacker-supplied password, then decrypt offline to recover the private key, enabling unauthorized transaction signing.

### Citations

**File:** core/web/router.go (L316-320)
```go
		authv2.GET("/keys/eth", ekc.Index)
		authv2.POST("/keys/eth", auth.RequiresEditRole(ekc.Create))
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
```

**File:** core/web/router.go (L337-356)
```go
		ocrkc := OCRKeysController{app}
		authv2.GET("/keys/ocr", ocrkc.Index)
		authv2.POST("/keys/ocr", auth.RequiresEditRole(ocrkc.Create))
		authv2.DELETE("/keys/ocr/:keyID", auth.RequiresAdminRole(ocrkc.Delete))
		authv2.POST("/keys/ocr/import", auth.RequiresAdminRole(ocrkc.Import))
		authv2.POST("/keys/ocr/export/:ID", auth.RequiresAdminRole(ocrkc.Export))

		ocr2kc := OCR2KeysController{app}
		authv2.GET("/keys/ocr2", ocr2kc.Index)
		authv2.POST("/keys/ocr2/:chainType", auth.RequiresEditRole(ocr2kc.Create))
		authv2.DELETE("/keys/ocr2/:keyID", auth.RequiresAdminRole(ocr2kc.Delete))
		authv2.POST("/keys/ocr2/import", auth.RequiresAdminRole(ocr2kc.Import))
		authv2.POST("/keys/ocr2/export/:ID", auth.RequiresAdminRole(ocr2kc.Export))

		p2pkc := P2PKeysController{app}
		authv2.GET("/keys/p2p", p2pkc.Index)
		authv2.POST("/keys/p2p", auth.RequiresEditRole(p2pkc.Create))
		authv2.DELETE("/keys/p2p/:keyID", auth.RequiresAdminRole(p2pkc.Delete))
		authv2.POST("/keys/p2p/import", auth.RequiresAdminRole(p2pkc.Import))
		authv2.POST("/keys/p2p/export/:ID", auth.RequiresAdminRole(p2pkc.Export))
```

**File:** core/web/auth/auth.go (L238-255)
```go
// RequiresAdminRole extracts the user object from the context, and asserts the user's role is 'admin'
func RequiresAdminRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role != clsessions.UserRoleAdmin {
			c.Abort()
			addForbiddenErrorHeaders(c, "admin", string(user.Role), user.Email)
			jsonAPIError(c, http.StatusForbidden, errors.New("Forbidden"))
			return
		}
		handler(c)
	}
}
```

**File:** core/web/keys_controller.go (L138-155)
```go
func (kc *keysController[K, R]) Export(c *gin.Context) {
	defer kc.lggr.ErrorIfFn(c.Request.Body.Close, "Error closing Export request body")

	keyID := c.Param("ID")
	newPassword := c.Query("newpassword")
	bytes, err := kc.ks.Export(keyID, newPassword)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	kc.auditLogger.Audit(audit.KeyExported, map[string]any{
		"type": kc.typ,
		"id":   keyID,
	})

	c.Data(http.StatusOK, MediaType, bytes)
}
```

**File:** core/web/eth_keys_controller.go (L240-258)
```go
func (ekc *ETHKeysController) Export(c *gin.Context) {
	defer ekc.app.GetLogger().ErrorIfFn(c.Request.Body.Close, "Error closing Export request body")

	id := c.Param("address")
	newPassword := c.Query("newpassword")

	bytes, err := ekc.app.GetKeyStore().Eth().Export(c.Request.Context(), id, newPassword)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	ekc.app.GetAuditLogger().Audit(audit.KeyExported, map[string]any{
		"type": "ethereum",
		"id":   id,
	})

	c.Data(http.StatusOK, MediaType, bytes)
}
```

**File:** core/web/user_controller.go (L243-273)
```go
// NewAPIToken generates a new API token for a user overwriting any pre-existing one set.
func (u *UserController) NewAPIToken(c *gin.Context) {
	ctx := c.Request.Context()
	var request clsession.ChangeAuthTokenRequest
	if err := c.ShouldBindJSON(&request); err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	sessionUser, ok := webauth.GetAuthenticatedUser(c)
	if !ok {
		jsonAPIError(c, http.StatusInternalServerError, errors.New("failed to obtain current user from context"))
		return
	}
	user, err := u.App.AuthenticationProvider().FindUser(ctx, sessionUser.Email)
	if err != nil {
		if errors.Is(err, clsession.ErrNotSupported) {
			jsonAPIError(c, http.StatusBadRequest, errUnsupportedForAuth)
			return
		}
		u.App.GetLogger().Errorf("failed to obtain current user record: %s", err)
		jsonAPIError(c, http.StatusInternalServerError, errors.New("unable to create API token"))
		return
	}
	// In order to create an API token, login validation with provided password must succeed
	err = u.App.AuthenticationProvider().TestPassword(ctx, sessionUser.Email, request.Password)
	if err != nil {
		u.App.GetAuditLogger().Audit(audit.APITokenCreateAttemptPasswordMismatch, map[string]any{"user": user.Email})
		jsonAPIError(c, http.StatusUnauthorized, errors.New("incorrect password"))
		return
	}
```

**File:** core/sessions/localauth/orm.go (L144-179)
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

	// No webauthn tokens registered for the current user, so normal authentication is now complete
	if len(uwas) == 0 {
		lggr.Infof("No MFA for user. Creating Session")
		session := sessions.NewSession()
		_, err = o.ds.ExecContext(ctx, "INSERT INTO sessions (id, email, last_used, created_at) VALUES ($1, $2, now(), now())", session.ID, user.Email)
		o.auditLogger.Audit(audit.AuthLoginSuccessNo2FA, map[string]any{"email": sr.Email})
		return session.ID, err
	}
```
