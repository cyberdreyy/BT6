### Title
View-role authenticated users can enroll a new WebAuthn/MFA credential without any role check or password re-verification - ([File: core/web/router.go], [File: core/web/webauthn_controller.go])

### Summary
The `/v2/enroll_webauthn` routes are registered under the generic `authv2` group, which only requires the caller to be authenticated (via session cookie or API token), with no `auth.RequiresEditRole`/`auth.RequiresAdminRole` wrapper and no step-up password confirmation, unlike other sensitive account-mutation endpoints. `WebAuthnController.BeginRegistration` and `FinishRegistration` immediately trust `auth.GetAuthenticatedUser(c)` and call `sessions.AddCredentialToUser`, so any authenticated session (including view-role) can add a persistent hardware-key credential to the account.

### Finding Description
In `core/web/router.go`, the WebAuthn enrollment endpoints are mounted like this: [1](#0-0) 

Compare this to the admin-only user-management routes registered just above them (`auth.RequiresAdminRole(uc.Index)`, etc.) in the same block: [2](#0-1) 

`enroll_webauthn` (`wa.BeginRegistration` and presumably the corresponding `POST /v2/enroll_webauthn` → `wa.FinishRegistration`) are attached directly to `authv2`, which only enforces `auth.Authenticate(... AuthenticateByToken, AuthenticateBySession)` — i.e., any valid, authenticated identity regardless of role (view, run, edit, admin) can reach these handlers. There is no `auth.RequiresEditRole` or `auth.RequiresAdminRole` wrapper on this route, unlike the `/users` admin endpoints.

Inside the controller, `BeginRegistration` and `FinishRegistration` (`core/web/webauthn_controller.go:32-104`) pull the user solely from `auth.GetAuthenticatedUser(c)` and proceed to generate a WebAuthn challenge and, on completion, persist the new credential via `sessions.AddCredentialToUser(ctx, ..., user.Email, credential)`: [3](#0-2) 

No re-authentication (e.g., recent password confirmation) is required before this sensitive, persistent account-security change is performed — the only gate is possession of a valid session/token, which is exactly the credential an attacker obtains via session hijacking (e.g., stolen cookie).

### Impact Explanation
An attacker who steals a session cookie or API token belonging to *any* role — including a low-privilege view-role user — can silently register their own WebAuthn authenticator against the victim's account. Because MFA credentials are persistent account artifacts, this grants the attacker a long-lived backdoor: they can subsequently use that hardware key as a second factor for the account even after the original stolen session/cookie expires or is revoked, enabling durable account takeover without ever learning the account password. This matches Chainlink's "authentication bypass / persistent account takeover via unauthorized credential injection" impact class.

### Likelihood Explanation
The precondition is only a valid session cookie or auth token for the target account, of any role — no admin, edit, or password knowledge is required. The `enroll_webauthn` endpoint is reachable directly via a two-step HTTP flow (`GET` to obtain the challenge, `POST` with a browser/authenticator-generated response) using standard WebAuthn client tooling, making this fully reproducible and repeatable for any hijacked session, not merely a theoretical edge case.

### Recommendation
Wrap `/v2/enroll_webauthn` (both `BeginRegistration` and `FinishRegistration`) with a role check appropriate to account-security changes (at minimum `auth.RequiresEditRole`, ideally restrict to the same user acting on their own account plus a step-up re-authentication requirement), and require a fresh/recent password confirmation before allowing `sessions.AddCredentialToUser` to execute — mirroring the pattern that should exist for other sensitive mutations like `NewAPIToken`/`UpdatePassword`.

### Proof of Concept
1. In `core/web/webauthn_controller_test.go` (or a new integration test), create a view-role user session (`sessions.ViewRole`), authenticate via `AuthenticateBySession`.
2. Call `GET /v2/enroll_webauthn` with that session and assert `200 OK` with a valid `PublicKeyCredentialCreationOptions` challenge is returned (demonstrating the missing role gate).
3. Simulate a `POST /v2/enroll_webauthn` with a forged/test WebAuthn attestation response (using the existing WebAuthn test helpers in `core/sessions/webauthn_test.go`) and assert `sessions.AddCredentialToUser` is invoked and a credential row is persisted for the victim's email — without any password/`TestPassword`-style re-authentication step in the request.
4. Assert failure: add a table test verifying that, absent a fix, no `auth.RequiresEditRole`/`RequiresAdminRole` or password-confirmation check blocks this flow, in contrast to `UserController.NewAPIToken`, which is expected to require `TestPassword`.

### Citations

**File:** core/web/router.go (L245-260)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	{
		uc := UserController{app}
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
		authv2.PATCH("/user/password", uc.UpdatePassword)
		authv2.POST("/user/token", uc.NewAPIToken)
		authv2.POST("/user/token/delete", uc.DeleteAPIToken)

		wa := NewWebAuthnController(app)
		authv2.GET("/enroll_webauthn", wa.BeginRegistration)
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
