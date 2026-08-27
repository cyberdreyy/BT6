### Title
Distinguishable authentication error responses in `SessionsController.Create`/`orm.CreateSession` enable user-account and MFA-status enumeration - ([File: core/web/sessions_controller.go], [File: core/sessions/localauth/orm.go])

### Summary
The unauthenticated `POST /sessions` endpoint returns different, unredacted error bodies depending on whether the submitted email exists, whether the password is wrong, or whether MFA is required, allowing an attacker to enumerate valid Chainlink node accounts and their MFA status without any credentials.

### Finding Description
`SessionsController.Create` binds the request and forwards it to `sc.App.AuthenticationProvider().CreateSession(ctx, sr)` [1](#0-0) . On error it returns the raw error text with HTTP 401 via `jsonAPIError(c, http.StatusUnauthorized, err)` [1](#0-0) , and `jsonAPIError` serializes `err.Error()` directly into the JSON response body [2](#0-1) .

Inside `orm.CreateSession`, the very first step calls `o.FindUser(ctx, sr.Email)` and, for a non-existent email, propagates the raw database error (`sql.ErrNoRows`, e.g. "sql: no rows in result set") straight back to the caller [3](#0-2) . If the email exists but the password is wrong, a distinct message "Invalid password" is returned [4](#0-3) . If the email/password are correct but MFA is enabled and no WebAuthn challenge data was supplied, a third distinct response shape (a JSON WebAuthn challenge object, non-error-like) is returned instead [5](#0-4) . These three cases are trivially distinguishable by body content and structure, giving three-way account/MFA-status classification: "email does not exist" vs "email exists, wrong password" vs "email exists, correct password, MFA challenge issued."

The code does contain intentional mitigation for one part of this problem: a comment notes that the password/email check happens before the WebAuthn lookup specifically "to prevent extra database look up for MFA tokens leaking if an account has MFA tokens or not" [6](#0-5) . This correctly prevents MFA status from leaking for wrong-password attempts. However, it does not address the more basic account-existence leak from `FindUser`'s raw error being returned unmodified, nor does it prevent MFA status from being revealed once the *correct* password is known (which requires the attacker to already have valid credentials, at which point MFA-status disclosure is a much smaller additional leak).

Separately, `SessionsController.Create` itself performs a `GetUserWebAuthn(ctx, sr.Email)` lookup before calling `CreateSession` [7](#0-6) ; this query runs unconditionally for every request (existing or not) and its result is not returned to the client directly, so on its own it does not add an additional oracle beyond what `CreateSession`'s own error text already exposes.

### Impact Explanation
This allows an unauthenticated attacker to enumerate whether a given email is a registered node operator/admin account by observing response body text ("sql: no rows in result set" vs "Invalid password"), which can be used to build a target list for credential-stuffing or phishing against real accounts. It does **not** disclose any credential, private key, session token, or secret material, and it does not bypass authentication or authorization — it is purely an information-disclosure/user-enumeration issue that could serve as a reconnaissance precursor. This falls short of the "critical key/credential exfiltration" impact class named in the question; it maps at most to a Low-severity "user enumeration" finding, since no session, credential, or protected resource is obtained directly from this behavior.

### Likelihood Explanation
No preconditions or privileges are required — the `/sessions` endpoint is explicitly unauthenticated (`unauth.POST("/sessions", sc.Create)`) [8](#0-7) , and only subject to a rate limiter. An attacker can trivially script differential requests against many candidate emails; the only friction is the configured unauthenticated rate limit.

### Recommendation
Normalize all `CreateSession` failure responses (nonexistent email, wrong password, and MFA-required-but-no-challenge) to a single generic error message and status code (e.g., "invalid credentials") returned to the client, while keeping distinguishing detail only in server-side audit logs (as is already done via `o.auditLogger.Audit(...)` calls). Avoid propagating raw ORM/database errors (like `sql.ErrNoRows`) through `jsonAPIError` for authentication endpoints.

### Proof of Concept
Go handler-level test plan (extending existing `TestSessionsController_Create` in `core/web/sessions_controller_test.go`):
1. Create one user with no MFA and one user with a WebAuthn token registered.
2. POST `/sessions` with (a) a non-existent email, (b) the no-MFA user's email with wrong password, (c) the MFA user's email with correct password and no `WebAuthnData`.
3. Assert response bodies differ meaningfully: (a) contains DB-level error text ("no rows"), (b) contains "Invalid password", (c) contains a WebAuthn challenge JSON structure (`protocol.CredentialAssertion`) rather than a plain error — confirming three distinguishable states are observable pre-authentication, matching `TestORM_WebAuthn` and `TestORM_CreateSession` behavior already present in `core/sessions/localauth/orm_test.go`.

### Citations

**File:** core/web/sessions_controller.go (L41-54)
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
```

**File:** core/web/sessions_controller.go (L56-60)
```go
	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
		return
	}
```

**File:** core/web/helpers.go (L21-29)
```go
func jsonAPIError(c *gin.Context, statusCode int, err error) {
	_ = c.Error(err).SetType(gin.ErrorTypePublic)
	var jsonErr *models.JSONAPIErrors
	if errors.As(err, &jsonErr) {
		c.JSON(statusCode, jsonErr)
		return
	}
	c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))
}
```

**File:** core/sessions/localauth/orm.go (L144-148)
```go
func (o *orm) CreateSession(ctx context.Context, sr sessions.SessionRequest) (string, error) {
	user, err := o.FindUser(ctx, sr.Email)
	if err != nil {
		return "", err
	}
```

**File:** core/sessions/localauth/orm.go (L152-157)
```go
	// Do email and password check first to prevent extra database look up
	// for MFA tokens leaking if an account has MFA tokens or not.
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		o.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid email")
	}
```

**File:** core/sessions/localauth/orm.go (L159-162)
```go
	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}
```

**File:** core/sessions/localauth/orm.go (L181-199)
```go
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

**File:** core/web/router.go (L210-215)
```go
	unauth := r.Group("/", rateLimiter(
		rl.UnauthenticatedPeriod(),
		rl.Unauthenticated(),
	))
	sc := NewSessionsController(app)
	unauth.POST("/sessions", sc.Create)
```
