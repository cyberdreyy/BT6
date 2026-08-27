### Title
Unrestricted-role disclosure of all node ETH/EVM wallet addresses via `GET /v2/keys/eth` and `GET /v2/keys/evm` - ([File: core/web/router.go])

### Summary
The chainlink node's key-management routes require only *authentication* (session or token) but omit any role check on the `Index` (list) handlers for ETH/EVM keys, while every other sensitive operation on the same resource (`Create`, `Delete`, `Import`, `Export`) is explicitly gated behind `auth.RequiresEditRole` or `auth.RequiresAdminRole`.

### Finding Description
In `v2Routes`, the ETH key endpoints are registered as: [1](#0-0) [2](#0-1) 

Note that `authv2.GET("/keys/eth", ekc.Index)` and `authv2.GET("/keys/evm", ekc.Index)` have no role-gating wrapper (`RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole`), unlike the sibling `POST`/`DELETE` routes on the same resource. The `authv2` group only enforces that a request is *authenticated* — either by session cookie or by API token — via: [3](#0-2) 

The role hierarchy defined in `core/web/auth/auth.go` is `View < Run < Edit < Admin`: [4](#0-3) 

`ETHKeysController.Index` itself performs no additional authorization check — it simply enumerates every configured EVM key/address (including disabled ones) and returns them with balances: [5](#0-4) 

This mirrors the reported bug class: the "low level" listing endpoint (`eth_getAddress` analog = `GET /v2/keys/eth|evm` / `ETHKeysController.Index`) does not apply the same consent/authorization gate that the "higher level" mutating endpoints on the identical resource enforce, allowing any authenticated principal — regardless of assigned role — to enumerate the full set of node wallet addresses that would otherwise only be selectively exposed to callers with elevated (`edit`/`admin`) privileges.

### Impact Explanation
Any user account with the minimal `view` role (the lowest privilege level, intended only for read access to non-sensitive operational data) can retrieve the complete list of the node's EVM wallet addresses along with live ETH/LINK balances. This is a broken access control / privilege-boundary issue: the node operator may intend `view`-role credentials (e.g. handed to a monitoring/read-only integration) to have no visibility into wallet address inventory, but the missing role check exposes this data anyway. Address enumeration of node signing keys can aid targeted phishing/social-engineering of the operator, fund-flow tracking, or planning of front-running/griefing attacks against known node addresses.

### Likelihood Explanation
High reachability: the endpoint is reachable by any principal that can authenticate to the node's HTTP API with valid session or API-token credentials, without needing edit/admin privileges — likelihood of exploitation is limited only by whether the deployment issues `view`-role credentials to less-trusted parties (a supported and documented use case for the `view` role).

### Recommendation
Wrap `authv2.GET("/keys/eth", ekc.Index)` and `authv2.GET("/keys/evm", ekc.Index)` with `auth.RequiresEditRole` (or at minimum `auth.RequiresRunRole`) to match the authorization level applied to the other key-management endpoints, ensuring `view`-role sessions/tokens cannot enumerate wallet addresses.

### Proof of Concept
1. Create a node user with role `view` (`POST /v2/users` by an admin, role=`view`), or issue a `view`-role API token.
2. Authenticate as that user (`POST /v2/sessions` or via token headers).
3. Call `GET /v2/keys/eth` or `GET /v2/keys/evm`.
4. Observe the full list of the node's EVM key addresses (and balances) is returned, despite the `view` role being the lowest privilege tier and other key-management operations (`Create`/`Delete`/`Import`/`Export`) being blocked for this role.

### Citations

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L315-320)
```go
		ekc := NewETHKeysController(app)
		authv2.GET("/keys/eth", ekc.Index)
		authv2.POST("/keys/eth", auth.RequiresEditRole(ekc.Create))
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
```

**File:** core/web/router.go (L330-335)
```go
		authv2.GET("/keys/evm", ekc.Index)
		ethKeysGroup.POST("/keys/evm", auth.RequiresEditRole(ekc.Create))
		ethKeysGroup.DELETE("/keys/evm/:address", auth.RequiresAdminRole(ekc.Delete))
		ethKeysGroup.POST("/keys/evm/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/evm/export/:address", auth.RequiresAdminRole(ekc.Export))
		ethKeysGroup.POST("/keys/evm/chain", auth.RequiresAdminRole(ekc.Chain))
```

**File:** core/web/auth/auth.go (L200-236)
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

// RequiresEditRole extracts the user object from the context, and asserts the user's role is at least
// 'edit'
func RequiresEditRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView || user.Role == clsessions.UserRoleRun {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}
```

**File:** core/web/eth_keys_controller.go (L83-117)
```go
func (ekc *ETHKeysController) Index(c *gin.Context) {
	ethKeyStore := ekc.app.GetKeyStore().Eth()
	var keys []ethkey.KeyV2
	var err error
	keys, err = ethKeyStore.GetAll(c.Request.Context())
	if err != nil {
		err = errors.Errorf("error getting unlocked keys: %v", err)
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}
	states, err := ethKeyStore.GetStatesForKeys(c.Request.Context(), keys)
	if err != nil {
		err = errors.Errorf("error getting key states: %v", err)
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}
	var resources []presenters.ETHKeyResource
	for _, state := range states {
		key, err := ethKeyStore.Get(c.Request.Context(), state.Address.Hex())
		if err != nil {
			jsonAPIError(c, http.StatusInternalServerError, err)
			return
		}

		r := createETHKeyResource(c, ekc, key, state)

		resources = append(resources, *r)
	}
	// Put disabled keys to the end
	sort.SliceStable(resources, func(i, j int) bool {
		return !resources[i].Disabled && resources[j].Disabled
	})

	jsonAPIResponseWithStatus(c, resources, "keys", http.StatusOK)
}
```
