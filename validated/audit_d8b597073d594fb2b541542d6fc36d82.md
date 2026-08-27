### Title
Persistent (Non-Session) Authentication Cookie Keeps Operator UI Logged In After Browser Close - ([File: core/services/chainlink/config_web_server.go])

### Summary
The Chainlink node's web UI/API authentication cookie is configured as a long-lived persistent cookie (`MaxAge: 86400 * 30`, i.e. 30 days) rather than a browser session cookie that would be cleared automatically when the browser is closed. As a result, an authenticated Operator UI session survives closing and reopening the browser, allowing anyone with access to the same browser profile/device to regain authenticated access without re-entering credentials, mirroring the reported "wallet not locked on browser close" issue.

### Finding Description
The router wires up the Gin session middleware with a cookie store configured via `SessionOptions()`: [1](#0-0) 

`MaxAge: 86400 * 30` explicitly instructs the browser to persist the authentication cookie on disk for 30 days, instead of omitting `MaxAge`/`Expires` (which would make it a true "session cookie" cleared when the browser process exits). This store is applied to all authenticated API/GUI/GraphQL routes: [2](#0-1) 

Once a user logs in, `SessionsController.Create` stores the session ID inside this persistent cookie: [3](#0-2) 

Server-side, the only thing gating continued authentication is an *idle* timeout check against `last_used`, defaulting to 15 minutes, and this window is refreshed on every authenticated request (including background polling by the SPA): [4](#0-3) 

Because the cookie itself is not cleared by the browser on close, and because `last_used` is refreshed by ordinary UI activity, closing and quickly reopening the browser (the exact PoC described in the report) reuses the still-present cookie and still-valid `sessions` row, restoring the authenticated state with no re-authentication prompt — functionally identical to the reported wallet issue, just applied to the Operator UI/API session rather than a browser-extension wallet.

### Impact Explanation
Any actor with subsequent access to the same browser profile (shared workstation, lost/stolen laptop, unattended session in a public/shared environment) can resume a fully authenticated Operator UI/API session without credentials, as long as the idle timeout window has not elapsed. Depending on the authenticated user's role, this can expose sensitive node configuration, job specs, keys management pages, and administrative actions.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to have physical/local access to the victim's already-authenticated browser profile (shared/public computer, unlocked/left device) — this matches the report's own threat model ("shared or public environments, or if the device is lost or compromised"). No network-layer or privileged access is needed; it's exploitable by any unprivileged local actor reusing the browser.

### Recommendation
Do not set a persistent `MaxAge`/`Expires` on the authentication cookie; use a true session cookie (cleared on browser close) in `SessionOptions()` in `core/services/chainlink/config_web_server.go`. Additionally, consider binding sessions more strictly to client/browser fingerprint, shortening default idle `SessionTimeout`, and providing an explicit "remember me" opt-in rather than defaulting all sessions to 30-day persistence.

### Proof of Concept
1. Log into the Chainlink Operator UI (`SessionsController.Create` sets the persistent cookie via `sessions.Options` with `MaxAge: 86400*30`).
2. Close the browser entirely.
3. Reopen the browser and navigate back to the node's UI URL within the `SessionTimeout` idle window.
4. Observe the UI remains authenticated with no login prompt, because the persistent cookie was retained by the browser and the corresponding `sessions` row in the DB is still valid per `findValidSession` in `core/sessions/localauth/orm.go`.

### Citations

**File:** core/services/chainlink/config_web_server.go (L164-175)
```go
func (w *webServerConfig) SecureCookies() bool {
	return *w.c.SecureCookies
}

func (w *webServerConfig) SessionOptions() sessions.Options {
	return sessions.Options{
		Secure:   w.SecureCookies(),
		HttpOnly: true,
		MaxAge:   86400 * 30,
		SameSite: http.SameSiteStrictMode,
	}
}
```

**File:** core/web/router.go (L52-85)
```go
	secret, err := app.SecretGenerator().Generate(config.RootDir())
	if err != nil {
		return nil, err
	}
	sessionStore := cookie.NewStore(secret)
	sessionStore.Options(config.WebServer().SessionOptions())
	cors := uiCorsHandler(config.WebServer().AllowOrigins())
	if prometheus != nil {
		prometheusUse(prometheus, engine, promhttp.HandlerOpts{EnableOpenMetrics: true})
	}

	tls := config.WebServer().TLS()
	engine.Use(
		otelgin.Middleware("chainlink-web-routes",
			otelgin.WithTracerProvider(otel.GetTracerProvider())),
		limits.RequestSizeLimiter(config.WebServer().HTTPMaxSize()),
		loggerFunc(app.GetLogger()),
		gin.Recovery(),
		cors,
		secureMiddleware(tls.ForceRedirect(), tls.Host(), config.Insecure().DevWebServer()),
	)
	if prometheus != nil {
		engine.Use(prometheus.Instrument())
	}
	engine.Use(helmet.Default())
	rl := config.WebServer().RateLimit()
	api := engine.Group(
		"/",
		rateLimiter(
			rl.AuthenticatedPeriod(),
			rl.Authenticated(),
		),
		sessions.Sessions(auth.SessionName, sessionStore),
	)
```

**File:** core/web/sessions_controller.go (L29-68)
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

	if err := saveSessionID(session, sid); err != nil {
		jsonAPIError(c, http.StatusInternalServerError, errors.Join(errors.New("unable to save session id"), err))
		return
	}

	jsonAPIResponse(c, Session{Authenticated: true}, "session")
}
```

**File:** core/sessions/localauth/orm.go (L68-107)
```go
// findValidSession finds an unexpired session by its ID and returns the associated email.
func (o *orm) findValidSession(ctx context.Context, sessionID string) (email string, err error) {
	if err := o.ds.GetContext(ctx, &email, "SELECT email FROM sessions WHERE id = $1 AND last_used + $2 >= now() FOR UPDATE", sessionID, o.sessionDuration); err != nil {
		o.lggr.Infof("query result: %v", email)
		return email, pkgerrors.Wrap(err, "no matching user for provided session token")
	}
	return email, nil
}

// updateSessionLastUsed updates a session by its ID and sets the LastUsed field to now().
func (o *orm) updateSessionLastUsed(ctx context.Context, sessionID string) error {
	_, err := o.ds.ExecContext(ctx, "UPDATE sessions SET last_used = now() WHERE id = $1", sessionID)
	return err
}

// AuthorizedUserWithSession will return the API user associated with the Session ID if it
// exists and hasn't expired, and update session's LastUsed field.
// AuthorizedUserWithSession will return the API user associated with the Session ID if it
// exists and hasn't expired, and update session's LastUsed field.
func (o *orm) AuthorizedUserWithSession(ctx context.Context, sessionID string) (user sessions.User, err error) {
	if len(sessionID) == 0 {
		return sessions.User{}, sessions.ErrEmptySessionID
	}

	email, err := o.findValidSession(ctx, sessionID)
	if err != nil {
		return sessions.User{}, sessions.ErrUserSessionExpired
	}

	user, err = o.findUser(ctx, email)
	if err != nil {
		return sessions.User{}, sessions.ErrUserSessionExpired
	}

	if err := o.updateSessionLastUsed(ctx, sessionID); err != nil {
		return sessions.User{}, err
	}

	return user, nil
}
```
