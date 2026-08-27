Confirmed: the `/v2/config` and `/v2/config/v2` routes are wired at [1](#0-0)  inside the `authv2` group, which only requires `auth.Authenticate` (session or token) with no `auth.RequiresEditRole`/`auth.RequiresAdminRole` wrapper, unlike neighboring routes such as `/keys/eth` (POST/DELETE) or `/users` which are explicitly role-gated [2](#0-1) . `ConfigController.Show` unconditionally calls `cfg.ConfigTOML()` and returns the full effective (or user) TOML with no role check inside the handler itself [3](#0-2) .

However, `ConfigTOML()` only returns `inputTOML`/`effectiveTOML` derived from the `Config` struct — separate from `Secrets`, which are stored/rendered via `secretsTOML` and never exposed through `ConfigTOML()` [4](#0-3) . Secrets (DB credentials, API keys, keystore passwords, etc.) live in the distinct `Secrets`/`secretsTOML` fields [5](#0-4)  and are not reachable via this endpoint. So while any authenticated user — including a `UserRoleView` session or scoped token — can indeed retrieve the full effective non-secret TOML configuration (network endpoints, feature flags, listen addresses, chain configs, internal topology settings), no secrets or credentials are disclosed by this specific code path.

### Title
Missing role check on `/v2/config` allows view-role users to read full effective node configuration - (File: core/web/config_controller.go)

### Summary
The `ConfigController.Show` handler is reachable by any authenticated user (including `UserRoleView`) because the routes `/v2/config` and `/v2/config/v2` are registered in `authv2` without a `RequiresEditRole`/`RequiresAdminRole` wrapper, unlike nearly all other sensitive endpoints in the same router. This returns the complete effective (non-secret) node TOML configuration to the lowest-privileged authenticated role.

### Finding Description
`v2Routes` registers `authv2.GET("/config", cc.Show)` and `authv2.GET("/config/v2", cc.Show)` inside the `authv2` group, which is gated only by `auth.Authenticate` with `AuthenticateByToken`/`AuthenticateBySession` — no role wrapper is applied, in contrast to sibling routes (e.g., `/users`, `/keys/eth` POST/DELETE) which use `auth.RequiresEditRole`/`auth.RequiresAdminRole`. Inside `ConfigController.Show`, the handler calls `cc.App.GetConfig()` then `cfg.ConfigTOML()` and serializes the resulting TOML (user or effective, based on the `userOnly` query param) directly into the JSON:API response with no role check performed in the handler body. Any session or API token holder — regardless of role (`UserRoleView`, `UserRoleRun`, `UserRoleEdit`, `UserRoleAdmin`) — can therefore call `GET /v2/config` and receive the full effective configuration, including chain RPC endpoints, feature flags, webserver settings, and other internal topology details normally intended for admin/edit-level visibility. This does not expose secrets, since `ConfigTOML()` returns only `inputTOML`/`effectiveTOML` from the `Config` struct, structurally separate from the redacted `Secrets`/`secretsTOML` fields, which are never returned by this endpoint.

### Impact Explanation
This corresponds to a low-severity information disclosure: an unprivileged-but-authenticated user (view-role) can enumerate the node's full non-secret operational configuration (RPC/chain endpoints, feature toggles, timeouts, listen addresses, capabilities/CRE settings, etc.), which could aid further reconnaissance or targeted attacks against the node's infrastructure. It does not directly yield secrets, private keys, or enable fund movement, so it falls short of a critical/high "secret disclosure" bounty class, but qualifies as a broken access-control / least-privilege violation, since the intent of role separation (`UserRoleView` vs `UserRoleEdit`/`Admin`) is bypassed for a sensitive read.

### Likelihood Explanation
Trivial and fully repeatable: any user with a valid session cookie or API token of any role (including `UserRoleView`) can send `GET /v2/config` and immediately receive a 200 response with the full config. No special timing, race, or chained exploit is required — this is a straightforward broken authorization check on a single endpoint.

### Recommendation
Wrap the `/config` and `/config/v2` routes with an appropriate role guard, e.g. `auth.RequiresAdminRole(cc.Show)` (or at minimum `auth.RequiresEditRole`), consistent with the sensitivity of full configuration disclosure, matching the pattern used for `/users`, `/keys/*` admin-only mutation routes.

### Proof of Concept
Go handler-level integration test (in `core/web/config_controller_test.go` style, using existing test harness `cltest.NewApplicationWithConfig`/`setupConfigTest`):
1. Build a test app and router as in existing controller tests (see patterns in `core/web/*_test.go` that create sessions with specific roles).
2. Create a user/session with `clsessions.UserRoleView` only (no admin/edit).
3. Issue `client.Get("/v2/config")` using that view-role session's authenticated HTTP client.
4. Assert response status is `200 OK` (not `403 Forbidden`), and that the JSON:API body's `data.attributes.config` field contains the full effective TOML (verify by comparing against `app.GetConfig()`'s `ConfigTOML()` effective output, or check for presence of known non-default fields).
5. Compare against expected behavior: add a companion assertion that mutation endpoints like `POST /v2/keys/eth` return `403` for the same view-role session, to highlight the inconsistency, and update the route wiring test to expect a role-gate rejection once patched.

### Citations

**File:** core/web/router.go (L251-254)
```go
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
```

**File:** core/web/router.go (L283-285)
```go
		cc := ConfigController{app}
		authv2.GET("/config", cc.Show)
		authv2.GET("/config/v2", cc.Show)
```

**File:** core/web/config_controller.go (L23-42)
```go
func (cc *ConfigController) Show(c *gin.Context) {
	cfg := cc.App.GetConfig()
	var userOnly bool
	if s, has := c.GetQuery("userOnly"); has {
		var err error
		userOnly, err = strconv.ParseBool(s)
		if err != nil {
			jsonAPIError(c, http.StatusBadRequest, fmt.Errorf("invalid bool for userOnly: %w", err))
			return
		}
	}
	var toml string
	user, effective := cfg.ConfigTOML()
	if userOnly {
		toml = user
	} else {
		toml = effective
	}
	jsonAPIResponse(c, ConfigV2Resource{toml}, "config")
}
```

**File:** core/services/chainlink/config_general.go (L30-36)
```go
	inputTOML     string // user input, normalized via de/re-serialization
	effectiveTOML string // with default values included
	secretsTOML   string // with env overrides includes, redacted

	c       *Config // all fields non-nil (unless the legacy method signature return a pointer)
	secrets *Secrets

```

**File:** core/services/chainlink/config_general.go (L287-290)
```go
// ConfigTOML implements chainlink.ConfigV2
func (g *generalConfig) ConfigTOML() (user, effective string) {
	return g.inputTOML, g.effectiveTOML
}
```
