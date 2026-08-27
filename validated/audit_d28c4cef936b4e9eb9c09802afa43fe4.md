### Title
View-role users can read full effective node configuration via GET /v2/config with no role check - ([File: core/web/router.go])

### Summary
The `GET /v2/config` and `/v2/config/v2` routes are registered without any `auth.RequiresXRole` wrapper, unlike almost every other route in the same block. `ConfigController.Show` returns the full effective/user TOML configuration via `cfg.ConfigTOML()`. Any authenticated user, including the lowest-privilege `view` role, can retrieve the entire node config, including EVM node RPC endpoints (`HTTPURL`/`WSURL`), which frequently embed provider API keys in the URL path/query.

### Finding Description
In `core/web/router.go`, the config routes are registered as: [1](#0-0) 

unlike sibling routes in the same block (e.g. `authv2.GET("/users", auth.RequiresAdminRole(uc.Index))`, `authv2.POST("/transfers", auth.RequiresAdminRole(...))`) which are wrapped with `RequiresAdminRole`/`RequiresEditRole`/`RequiresRunRole`. `auth.Authenticate` (`core/web/auth/auth.go`) only verifies identity (session or token) and sets the user in context — it performs no role check itself: [2](#0-1) 

Role checks are only enforced by the explicit `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` wrappers, e.g.: [3](#0-2) 

Since `/config` and `/config/v2` have none of these wrappers, any user authenticated by session or API token — including one with `UserRoleView` — passes through and reaches `cc.Show`: [4](#0-3) 

`Show` calls `cfg.ConfigTOML()` which returns `(inputTOML, effectiveTOML)` from `generalConfig`: [5](#0-4) 

`generalConfig.effectiveTOML`/`inputTOML` are built from the non-secret `Config` struct only; the true `Secrets` struct (DB DSN, keystore password) is stored separately (`g.secrets`, `g.secretsTOML`) and is never returned by `ConfigTOML()` or exposed through this endpoint — so DB passwords/keystore passwords are not directly leaked this way. However, the `Config` (non-secrets) TOML legitimately includes `EVM.Nodes[].HTTPURL` / `WSURL`, which is where operators commonly embed RPC provider API keys (e.g., Alchemy/Infura URLs), as shown throughout the config fixtures (`core/services/chainlink/testdata/config-full.toml:643-657`, `core/config/docs/chains-evm.toml:571-588`). These URLs are returned verbatim, unredacted, to any authenticated caller regardless of role.

### Impact Explanation
A `view`-role credential (the lowest privilege level, meant only for read-only dashboard access) or a restricted API token with view role can retrieve the node's complete effective configuration, including RPC endpoint URLs that may contain embedded provider API keys, chain configuration internals, and other operational details not intended for low-privilege roles. This is an authorization-boundary violation: the endpoint is reachable by any authenticated identity when the codebase's own convention (seen on every neighboring route) is to gate sensitive/administrative data behind `RequiresEditRole`/`RequiresAdminRole`. This maps to a scoped "sensitive information disclosure to unauthorized role" impact — it does not expose the DB DSN or keystore password (those live in `Secrets`, which is not returned), but it does expose RPC provider keys embedded in URLs, which could allow API-key theft/quota abuse.

### Likelihood Explanation
Trivial and fully reproducible: requires only a valid `view`-role user session/API token (the minimal, least-privileged role in the system) and a single `GET /v2/config` request — no additional exploitation steps, timing, or race conditions needed.

### Recommendation
Wrap the config routes with an explicit role requirement consistent with their sensitivity, e.g. `authv2.GET("/config", auth.RequiresAdminRole(cc.Show))` (or at minimum `RequiresEditRole`), and/or redact known-sensitive TOML fields (e.g., `EVM.Nodes[].HTTPURL`/`WSURL` query strings/API keys) in `ConfigController.Show` before returning them to non-admin roles.

### Proof of Concept
Handler-level test plan (extending `core/web/config_controller_test.go`-style tests / `TestRendererTable_RenderConfigurationV2` pattern in `core/cmd/renderer_test.go:33-58`):
1. Start an app via `cltest.NewApplicationEVMDisabled` with an `EVM.Nodes[].HTTPURL` set to a URL containing a fake API key path segment (e.g. `https://eth-mainnet.g.alchemy.com/v2/SECRET_KEY_ABC`).
2. Create a user with `sessions.UserRoleView` and authenticate an HTTP client as that view-role user (via `app.NewHTTPClient(&cltest.User{Role: sessions.UserRoleView})` or equivalent test helper).
3. Issue `GET /v2/config` and `GET /v2/config/v2` with the view-role client.
4. Assert `resp.StatusCode == http.StatusOK` (currently succeeds — expected to fail with `403 Forbidden` after fix).
5. Parse the JSON:API response into `web.ConfigV2Resource` and assert `strings.Contains(resource.Config, "SECRET_KEY_ABC")` is true (demonstrating the leak) pre-fix, and assert the request is rejected (or the field redacted) post-fix.
6. Repeat with an admin-role client to confirm intended admins retain access — establishing the differential behavior currently missing.

### Citations

**File:** core/web/router.go (L283-285)
```go
		cc := ConfigController{app}
		authv2.GET("/config", cc.Show)
		authv2.GET("/config/v2", cc.Show)
```

**File:** core/web/auth/auth.go (L155-175)
```go
// Authenticate is middleware which authenticates the request by attempting to
// authenticate using all the provided methods.
func Authenticate(store Authenticator, methods ...authMethod) gin.HandlerFunc {
	return func(c *gin.Context) {
		var err error
		for _, method := range methods {
			err = method(c, store)
			if !errors.Is(err, auth.ErrorAuthFailed) {
				break
			}
		}
		if err != nil {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, err)

			return
		}

		c.Next()
	}
}
```

**File:** core/web/auth/auth.go (L200-217)
```go
// RequiresRunRole extracts the user object from the context, and asserts the user's role is at least
// 'run'
func RequiresRunRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}
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

**File:** core/services/chainlink/config_general.go (L287-290)
```go
// ConfigTOML implements chainlink.ConfigV2
func (g *generalConfig) ConfigTOML() (user, effective string) {
	return g.inputTOML, g.effectiveTOML
}
```
