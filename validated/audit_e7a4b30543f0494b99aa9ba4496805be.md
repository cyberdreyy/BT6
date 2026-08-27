### Title
Login endpoint (`/sessions`) leaks raw database/driver error text to unauthenticated callers - ([File: core/sessions/localauth/orm.go])

### Summary
The chainlink node's login flow (`POST /v2/sessions`, handled by `SessionsController.Create`) calls `CreateSession`, which calls `FindUser` -> `findUser`, which executes a raw SQL query and returns the underlying `sql`/driver error verbatim: `err = o.ds.GetContext(ctx, &user, sql, email); return`. That raw error propagates unmodified back through `CreateSession` (`user, err := o.FindUser(ctx, sr.Email); if err != nil { return "", err }`) all the way to `SessionsController.Create`, where it is passed straight to `jsonAPIError(c, http.StatusUnauthorized, err)`, and ultimately serialized into the JSON API HTTP response body via `models.NewJSONAPIErrorsWith(err.Error())`. This is the exact bug class described in the Curio advisory: verbose driver/database error text reaching an HTTP client, potentially exposing DSN fragments, hostnames, or other backend details contained in low-level driver errors.

### Finding Description [1](#0-0) 
`FindUser`/`findUser` run parameterized queries and return the raw `error` from `sqlutil.DataSource.GetContext` without wrapping, sanitizing, or classifying it. [2](#0-1) 
`CreateSession` calls `o.FindUser(ctx, sr.Email)` and, on error, does `return "", err` — forwarding whatever the ORM returned (including raw driver/connection errors) unmodified. Contrast this with the rest of the function, which deliberately returns generic sanitized errors like `"Invalid email"`, `"Invalid password"`, and `"MFA Error"` for all other failure paths — showing that the intended design is to never leak internal detail, but the `FindUser` failure path was missed. [3](#0-2) 
`SessionsController.Create` is the `/v2/sessions` login handler, reachable pre-authentication by design (it exists to authenticate the caller). On `CreateSession` failure it calls `jsonAPIError(c, http.StatusUnauthorized, err)` with the error returned above. [4](#0-3) 
`jsonAPIError` serializes `err.Error()` directly into the JSON response body sent to the HTTP client (`models.NewJSONAPIErrorsWith(err.Error())`), with no filtering for sensitive substrings (unlike Curio's remediation which added an `errFilter` to redact password/host/port/`://` patterns).

This mirrors the Curio root cause precisely: a low-level database/driver error, which can embed connection-string fragments or other backend-internal detail depending on the driver/error type, is forwarded through `err.Error()` all the way into an HTTP error response with no redaction layer.

### Impact Explanation
An attacker who can reach the login endpoint (which requires no prior authentication — this is the login flow itself) can attempt to trigger database-level failures during the `findUser` query (e.g., by inducing connection pool exhaustion, timeouts, or malformed queries triggered via crafted input) and receive the raw driver error text in the HTTP response. Depending on error type and driver, this can expose internal details about the database backend (e.g., connection parameters, internal query fragments, or infrastructure information) that should remain server-side only, aiding further reconnaissance or credential/target-discovery for a follow-on attack. This is lower severity than Curio's finding because chainlink's DB connection URL is stored as a `models.SecretURL`/redacted type elsewhere in config handling [5](#0-4) , so the password itself is less likely to leak through this specific path than in Curio's plaintext-DSN construction — but the general class of exposing unsanitized backend error text to an HTTP client at an unauthenticated endpoint is the same defect.

### Likelihood Explanation
Reaching this code path requires only sending a request to `/v2/sessions`, which is an unauthenticated, internet-facing (if exposed) endpoint by design. Reliably forcing the specific error type needed to leak sensitive substrings (vs. generic "no rows" which is not itself sensitive) requires triggering an actual DB/driver-level failure rather than a simple "user not found," which is comparatively less trivial to reproduce deterministically. Likelihood is assessed as low-to-moderate: the code path is trivially reachable, but weaponizing it to reliably extract sensitive information depends on producing specific database failure modes.

### Recommendation
- In `findUser`/`FindUser` (`core/sessions/localauth/orm.go`), do not return raw driver errors to callers that ultimately reach HTTP responses; wrap and classify errors (e.g., distinguish `sql.ErrNoRows` from true infrastructure failures) and return a generic sentinel/error for the latter.
- In `CreateSession`, replace `return "", err` after `FindUser` failure with a generic error (e.g., `"Invalid email"` or an internal-error sentinel), consistent with the rest of the function's pattern, and log the detailed error server-side only.
- Add a defense-in-depth redaction layer (similar to Curio's `errFilter`) in `jsonAPIError` (`core/web/helpers.go`) / `core/web/auth/helpers.go` that inspects outgoing error text for sensitive substrings (`password`, `host`, `port`, `://`) before serialization, to protect other yet-unaudited callers of `jsonAPIError(c, http.StatusInternalServerError, err)` scattered across `core/web/*_controller.go`.

### Proof of Concept
1. Force the underlying database connection/query used by `findUser` to fail transiently (e.g., via connection pool exhaustion, statement timeout, or a backend outage window) while the node's `/v2/sessions` endpoint is reachable.
2. Send `POST /v2/sessions` with any `{"email": "...", "password": "..."}` payload during the failure window.
3. Observe the HTTP response body: instead of returning the code's intended generic error (e.g., `"Invalid email"`), the server returns the raw driver/database error text via `jsonAPIError(c, http.StatusUnauthorized, err)` → `models.NewJSONAPIErrorsWith(err.Error())`, sourced directly from `core/sessions/localauth/orm.go:56-58`'s unwrapped `err` value.

### Citations

**File:** core/sessions/localauth/orm.go (L43-59)
```go
// FindUser will attempt to return an API user by email.
func (o *orm) FindUser(ctx context.Context, email string) (sessions.User, error) {
	return o.findUser(ctx, email)
}

// FindUserByAPIToken will attempt to return an API user via the user's table token_key column.
func (o *orm) FindUserByAPIToken(ctx context.Context, apiToken string) (user sessions.User, err error) {
	sql := "SELECT * FROM users WHERE token_key = $1"
	err = o.ds.GetContext(ctx, &user, sql, apiToken)
	return
}

func (o *orm) findUser(ctx context.Context, email string) (user sessions.User, err error) {
	sql := "SELECT * FROM users WHERE lower(email) = lower($1)"
	err = o.ds.GetContext(ctx, &user, sql, email)
	return
}
```

**File:** core/sessions/localauth/orm.go (L144-157)
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
```

**File:** core/web/sessions_controller.go (L29-60)
```go
func (sc *SessionsController) Create(c *gin.Context) {
	defer sc.App.WakeSessionReaper()
	ctx := c.Request.Context()
	sc.App.GetLogger().Debugf("TRACE: Starting Session Creation")

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
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
		return
	}
```

**File:** core/web/helpers.go (L19-29)
```go
// jsonAPIError adds an error to the gin context and sets
// the JSON value of errors.
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

**File:** core/config/toml/types.go (L524-542)
```go
}

func (d *DatabaseSecrets) validateConfig(buildMode string) (err error) {
	if d.URL == nil || (*url.URL)(d.URL).String() == "" {
		err = errors.Join(err, configutils.ErrEmpty{Name: "URL", Msg: "must be provided and non-empty"})
	} else if *d.AllowSimplePasswords && buildMode == build.Prod {
		err = errors.Join(err, configutils.ErrInvalid{Name: "AllowSimplePasswords", Value: true, Msg: "insecure configs are not allowed on secure builds"})
	} else if !*d.AllowSimplePasswords {
		if verr := validateDBURL((url.URL)(*d.URL)); verr != nil {
			err = errors.Join(err, configutils.ErrInvalid{Name: "URL", Value: "*****", Msg: dbURLPasswordComplexity(verr)})
		}
	}
	if d.BackupURL != nil && !*d.AllowSimplePasswords {
		if verr := validateDBURL((url.URL)(*d.BackupURL)); verr != nil {
			err = errors.Join(err, configutils.ErrInvalid{Name: "BackupURL", Value: "*****", Msg: dbURLPasswordComplexity(verr)})
		}
	}
	return err
}
```
