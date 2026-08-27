This confirms the vulnerability. In the local auth (`localauth/orm.go`) implementation, `CreateSession` explicitly loads `GetUserWebAuthn` and enforces the challenge/response flow via `sessions.BeginWebAuthnLogin`/`FinishWebAuthnLogin` when `len(uwas) > 0` [1](#0-0) . In contrast, the OIDC authenticator's `CreateSession` never queries WebAuthn data at all — it just calls `localLoginFallback`, which only validates email and password, then immediately creates a session [2](#0-1) [3](#0-2) . The `GetUserWebAuthn` method for the OIDC provider is hardcoded to return an empty slice unconditionally [4](#0-3) , and this is also what `SessionsController.Create` calls to decide whether to require WebAuthn [5](#0-4) . Since the controller's MFA gating and the OIDC `CreateSession`'s MFA gating are both driven by this stubbed method (and `CreateSession` itself has no independent enforcement), any registered WebAuthn credential for the local admin user is silently ignored when `AuthenticationMethod = oidc`.

### Title
OIDC-mode local admin login bypasses WebAuthn/MFA enforcement entirely - (File: core/sessions/oidcauth/oidc.go)

### Summary
When `WebServer.AuthenticationMethod` is set to `oidc`, the local admin login fallback path (`oidcAuthenticator.CreateSession` → `localLoginFallback`) authenticates users with email+password only and never checks or enforces WebAuthn/MFA, because `GetUserWebAuthn` is hardcoded to return an empty slice. This defeats MFA even for local admin users who have WebAuthn credentials registered in the `web_authns` table.

### Finding Description
`SessionsController.Create` (`core/web/sessions_controller.go:42-54`) determines whether to enforce a WebAuthn challenge by calling `sc.App.AuthenticationProvider().GetUserWebAuthn(ctx, sr.Email)` and only wiring up `sr.SessionStore`/`sr.WebAuthnConfig` if the returned slice is non-empty. When the configured `AuthenticationProvider` is the `oidcAuthenticator`, `GetUserWebAuthn` unconditionally returns `[]clsessions.WebAuthn{}, nil` regardless of what's actually stored in the `web_authns` table for that email (`core/sessions/oidcauth/oidc.go:404-407`, comment: "MFA is delegated to SAML provider"). Consequently `userWebAuthnTokens` is always empty, `sr.SessionStore`/`sr.WebAuthnConfig` are never populated, and the flow proceeds straight to `AuthenticationProvider().CreateSession`.

`oidcAuthenticator.CreateSession` (`core/sessions/oidcauth/oidc.go:412-439`) itself has no MFA logic at all — it calls `localLoginFallback`, which only performs a constant-time email compare and `utils.CheckPasswordHash` (`core/sessions/oidcauth/oidc.go:580-597`), then immediately inserts a row into `oidc_sessions` and returns a valid session ID. There is no code path in the OIDC authenticator that reads `web_authns`, calls `sessions.BeginWebAuthnLogin`/`FinishWebAuthnLogin`, or checks `sr.WebAuthnData` at all — unlike the local-auth `orm.CreateSession` which performs exactly this enforcement (`core/sessions/localauth/orm.go:164-211`).

This means that once a node operator switches `AuthenticationMethod` to `oidc` for its OIDC/SSO benefits, any pre-existing local admin user's WebAuthn 2FA registration becomes silently non-functional for the local-login fallback path — an attacker who obtains only the admin password (e.g., via phishing, credential stuffing, or password reuse) can authenticate as a full admin without possessing the registered hardware/software MFA token.

### Impact Explanation
This is an authentication bypass of a security control (MFA/2FA) that the operator explicitly enabled and that the shared `web_authns` schema and `localauth` implementation actively enforce. An attacker possessing only the admin password (not the second factor) obtains a fully authenticated admin session via `POST /sessions`, gaining complete node administrative control — including key management, job management, and other admin-only operations — which would otherwise require the second factor. This matches the "authentication bypass" bounty impact class.

### Likelihood Explanation
Preconditions are realistic: an operator enables `AuthenticationMethod = oidc` (a supported, documented configuration) while retaining local admin users with WebAuthn credentials already registered (e.g., migrated from local-auth mode or created for the CLI-fallback flow). No special attacker role is required beyond knowledge of the admin's plaintext password — the exact scenario MFA exists to mitigate. The bypass is deterministic and fully repeatable (`GetUserWebAuthn` always returns empty regardless of DB contents), requiring no race condition or timing dependency.

### Recommendation
Implement `oidcAuthenticator.GetUserWebAuthn` to actually query the shared `web_authns` table (as `localauth.orm.GetUserWebAuthn` does), and update `oidcAuthenticator.CreateSession`/`localLoginFallback` to enforce the WebAuthn challenge/response flow (`sessions.BeginWebAuthnLogin`/`FinishWebAuthnLogin`) when credentials exist for the local admin user, mirroring `core/sessions/localauth/orm.go:164-211`.

### Proof of Concept
1. Seed a local admin user in `users` with a known password hash, and a corresponding row in `web_authns` for that email (`public_key_data` from a registered credential), using the same helpers as `core/sessions/localauth/orm_test.go`.
2. Configure the application/test harness with `WebServer.AuthenticationMethod = oidc` so `chainlink.Application.AuthenticationProvider()` returns an `oidcAuthenticator` backed by the shared datastore.
3. Instantiate `SessionsController` with this app and call `Create` (or POST to `/sessions`) with a JSON body containing the correct `email`/`password` and no `WebAuthnData`.
4. Assert (current buggy behavior) that the response is `200 OK` with `Session{Authenticated: true}` and a valid session cookie — i.e., no WebAuthn challenge (`401` with challenge JSON) is ever returned, unlike the equivalent test against `localauth.orm.CreateSession` with the same seeded WebAuthn row, which returns a `401`/challenge per `core/sessions/localauth/orm.go:181-199`.
5. Expected/fixed behavior: the OIDC path should return `401` with a WebAuthn challenge JSON body when `WebAuthnData` is absent and a credential exists, matching local-auth semantics.

### Citations

**File:** core/sessions/localauth/orm.go (L164-199)
```go
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

	// Next check if this session request includes the required WebAuthn challenge data
	// if not, return a 401 error for the frontend to prompt the user to provide this
	// data in the next round trip request (tap key to include webauthn data on the login page)
	if sr.WebAuthnData == "" {
		lggr.Warnf("Attempted login to MFA user. Generating challenge for user.")
		options, webauthnError := sessions.BeginWebAuthnLogin(user, uwas, sr)
		if webauthnError != nil {
			lggr.Errorf("Could not begin WebAuthn verification: %v", webauthnError)
			return "", pkgerrors.New("MFA Error")
		}

		j, jsonError := json.Marshal(options)
		if jsonError != nil {
			lggr.Errorf("Could not serialize WebAuthn challenge: %v", jsonError)
			return "", pkgerrors.New("MFA Error")
		}

		return "", pkgerrors.New(string(j))
	}
```

**File:** core/sessions/oidcauth/oidc.go (L404-407)
```go
// GetUserWebAuthn returns an empty stub, MFA is delegated to SAML provider
func (oi *oidcAuthenticator) GetUserWebAuthn(ctx context.Context, email string) ([]clsessions.WebAuthn, error) {
	return []clsessions.WebAuthn{}, nil
}
```

**File:** core/sessions/oidcauth/oidc.go (L412-439)
```go
func (oi *oidcAuthenticator) CreateSession(ctx context.Context, sr clsessions.SessionRequest) (string, error) {
	foundUser, err := oi.localLoginFallback(ctx, sr)
	if err != nil {
		return "", err
	}

	sanitizedEmail := strings.ReplaceAll(sr.Email, "\n", "")
	sanitizedEmail = strings.ReplaceAll(sanitizedEmail, "\r", "")
	oi.lggr.Infof("Successful local admin login request for user %s - %s", sanitizedEmail, foundUser.Role)

	// Save local admin session, user, and role to sessions table
	// Sessions are set to expire after the duration + creation date elapsed
	session := clsessions.NewSession()
	_, err = oi.ds.ExecContext(ctx,
		"INSERT INTO oidc_sessions (id, user_email, user_role, created_at) VALUES ($1, $2, $3, now())",
		session.ID,
		strings.ToLower(sr.Email),
		foundUser.Role,
	)
	if err != nil {
		oi.lggr.Errorf("unable to create new session in oidc_sessions table %v", err)
		return "", fmt.Errorf("error creating local OIDC session: %w", err)
	}

	oi.auditLogger.Audit(audit.AuthLoginSuccessNo2FA, map[string]any{"email": sr.Email})

	return session.ID, nil
}
```

**File:** core/sessions/oidcauth/oidc.go (L578-597)
```go
// localLoginFallback tests the credentials provided against the 'local' authentication method
// This covers the case of local CLI API calls requiring local login separate from the OIDC server
func (oi *oidcAuthenticator) localLoginFallback(ctx context.Context, sr clsessions.SessionRequest) (clsessions.User, error) {
	var user clsessions.User
	err := oi.ds.GetContext(ctx, &user, SQLSelectUserbyEmail, sr.Email)
	if err != nil {
		return user, err
	}
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		oi.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return user, errors.New("invalid email")
	}

	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		oi.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return user, errors.New("invalid password")
	}

	return user, nil
}
```

**File:** core/web/sessions_controller.go (L41-56)
```go
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
