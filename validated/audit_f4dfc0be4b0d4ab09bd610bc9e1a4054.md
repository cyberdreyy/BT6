### Title
Missing role check on `/debug/vars` allows any authenticated view-role session user to read internal expvar runtime/config state - ([File: core/web/router.go])

### Summary
The `/debug` route group is wrapped only with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`, with no `auth.RequiresAdminRole` (or any role) wrapper, unlike other sensitive endpoints in the router. Any logged-in user with the lowest privilege (`view` role) can therefore hit `GET /debug/vars` and read the Go `expvar` package's published state, including `cmdline` (process argv) and `memstats`.

### Finding Description
`debugRoutes` in `core/web/router.go` sets up the group as:
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
``` [1](#0-0) 

`auth.Authenticate` runs the provided auth methods and, once one succeeds, calls `c.Next()` without any role inspection — it only checks that a valid session/user exists, not the user's role: [2](#0-1) 

Compare this to essentially every other sensitive handler in the same router file, which explicitly wraps handlers with `auth.RequiresAdminRole`, `auth.RequiresEditRole`, or `auth.RequiresRunRole` (e.g. `authv2.PATCH("/log", auth.RequiresAdminRole(lgc.Patch))`, `authv2.GET("/keys/eth", ekc.Index)` guarded at a higher tier, etc.) — see the full `v2Routes` wiring [3](#0-2) . `RequiresAdminRole`/`RequiresRunRole`/`RequiresEditRole` explicitly check `user.Role` and reject `view`/`run` roles for privileged actions [4](#0-3) .

Because `debugRoutes` skips any such role wrapper, a `view`-role user (the lowest privilege session role, defined in `clsessions.UserRoleView`) who successfully authenticates via session cookie can call `GET /debug/vars` and receive Go's `expvar` JSON output. This handler is the standard `net/http`/`gin-contrib/expvar` default handler, which by default publishes `cmdline` (the process's `os.Args`, e.g. binary path/flags) and `memstats` (detailed runtime memory statistics) with no additional filtering.

### Impact Explanation
This is an authorization/role-check bypass: a low-privileged (`view`) authenticated user gains access to a debug endpoint that, by convention elsewhere in this router, should require elevated privilege. The exposed data (`cmdline`, `memstats`) can leak node startup arguments/paths and detailed internal runtime characteristics useful for reconnaissance/fingerprinting the node, which is inconsistent with the principle of least privilege enforced for comparable debug/introspection endpoints (`/v2/log`, `/v2/config`, pprof routes gated behind `authv2`/admin). This falls under information disclosure of internal node state to a low-privileged actor.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs a valid `view`-role session (the lowest role grantable to a Chainlink node user) — no admin, edit, or run privileges are required. The request is a simple unauthenticated-role `GET /debug/vars` and is trivially repeatable.

### Recommendation
Wrap the `/debug` group (and any similarly-exposed debug endpoints) with `auth.RequiresAdminRole` (or at minimum `RequiresEditRole`) consistent with how other sensitive introspection endpoints (`/v2/log`, pprof endpoints under `authv2`) are protected, so that only privileged roles can access `expvar` output.

### Proof of Concept
1. Add a Go handler-level integration test in `core/web` (alongside existing router tests) that:
   - Creates a test `chainlink.Application` with a seeded user of role `clsessions.UserRoleView`.
   - Logs in via `POST /sessions` to obtain a session cookie (or directly sets the session as done in existing session controller tests).
   - Issues `GET /debug/vars` with that session cookie.
   - Asserts the response status is `200 OK` (not `401`/`403`).
   - Unmarshals the JSON body and asserts it contains keys `cmdline` and `memstats`.
2. Add a comparison test hitting an admin-gated endpoint (e.g. `GET /v2/log`) with the same `view`-role session and assert it returns `401 Unauthorized`, demonstrating the inconsistency: `/debug/vars` is reachable by `view` role while comparable debug/introspection endpoints are not.

### Citations

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
```

**File:** core/web/router.go (L251-443)
```go
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
		authv2.PATCH("/user/password", uc.UpdatePassword)
		authv2.POST("/user/token", uc.NewAPIToken)
		authv2.POST("/user/token/delete", uc.DeleteAPIToken)

		wa := NewWebAuthnController(app)
		authv2.GET("/enroll_webauthn", wa.BeginRegistration)
		authv2.POST("/enroll_webauthn", wa.FinishRegistration)

		eia := ExternalInitiatorsController{app}
		authv2.GET("/external_initiators", paginatedRequest(eia.Index))
		authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
		authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))

		bt := BridgeTypesController{app}
		authv2.GET("/bridge_types", paginatedRequest(bt.Index))
		authv2.POST("/bridge_types", auth.RequiresEditRole(bt.Create))
		authv2.GET("/bridge_types/:BridgeName", bt.Show)
		authv2.PATCH("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Update))
		authv2.DELETE("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Destroy))

		ets := EVMTransfersController{app}
		authv2.POST("/transfers", auth.RequiresAdminRole(ets.Create))
		authv2.POST("/transfers/evm", auth.RequiresAdminRole(ets.Create))
		tts := CosmosTransfersController{app}
		authv2.POST("/transfers/cosmos", auth.RequiresAdminRole(tts.Create))
		sts := SolanaTransfersController{app}
		authv2.POST("/transfers/solana", auth.RequiresAdminRole(sts.Create))

		cc := ConfigController{app}
		authv2.GET("/config", cc.Show)
		authv2.GET("/config/v2", cc.Show)

		tas := TxAttemptsController{app}
		authv2.GET("/tx_attempts", paginatedRequest(tas.Index))
		authv2.GET("/tx_attempts/evm", paginatedRequest(tas.Index))

		txs := TransactionsController{app}
		authv2.GET("/transactions/evm", paginatedRequest(txs.Index))
		authv2.GET("/transactions/evm/:TxHash", txs.Show)
		authv2.GET("/transactions", paginatedRequest(txs.Index))
		authv2.GET("/transactions/:TxHash", txs.Show)

		rc := ReplayController{app}
		authv2.POST("/replay_from_block/:number", auth.RequiresRunRole(rc.ReplayFromBlock))
		lcaC := LCAController{app}
		authv2.GET("/find_lca", auth.RequiresRunRole(lcaC.FindLCA))
		lpSkipC := LPSkipController{app}
		authv2.POST("/lp_skip_to_block", auth.RequiresRunRole(lpSkipC.LPSkipToBlock))

		if build.IsDev() {
			capContr := CapabilityController{app}
			authv2.POST("/execute_capability", auth.RequiresRunRole(capContr.ExecuteCapability))
		}

		csakc := CSAKeysController{app}
		authv2.GET("/keys/csa", csakc.Index)
		authv2.POST("/keys/csa", auth.RequiresEditRole(csakc.Create))
		authv2.POST("/keys/csa/import", auth.RequiresAdminRole(csakc.Import))
		authv2.POST("/keys/csa/export/:ID", auth.RequiresAdminRole(csakc.Export))

		ekc := NewETHKeysController(app)
		authv2.GET("/keys/eth", ekc.Index)
		authv2.POST("/keys/eth", auth.RequiresEditRole(ekc.Create))
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
		// duplicated from above, with `evm` instead of `eth`
		// legacy ones remain for backwards compatibility

		ethKeysGroup := authv2.Group("", auth.Authenticate(app.AuthenticationProvider(),
			auth.AuthenticateByToken,
			auth.AuthenticateBySession,
		))

		ethKeysGroup.Use(ekc.formatETHKeyResponse())
		authv2.GET("/keys/evm", ekc.Index)
		ethKeysGroup.POST("/keys/evm", auth.RequiresEditRole(ekc.Create))
		ethKeysGroup.DELETE("/keys/evm/:address", auth.RequiresAdminRole(ekc.Delete))
		ethKeysGroup.POST("/keys/evm/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/evm/export/:address", auth.RequiresAdminRole(ekc.Export))
		ethKeysGroup.POST("/keys/evm/chain", auth.RequiresAdminRole(ekc.Chain))

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

		for _, keys := range []struct {
			path string
			kc   KeysController
		}{
			{"solana", NewSolanaKeysController(app)},
			{"cosmos", NewCosmosKeysController(app)},
			{"starknet", NewStarkNetKeysController(app)},
			{"aptos", NewAptosKeysController(app)},
			{"stellar", NewStellarKeysController(app)},
			{"tron", NewTronKeysController(app)},
			{"sui", NewSuiKeysController(app)},
			{"ton", NewTONKeysController(app)},
		} {
			authv2.GET("/keys/"+keys.path, keys.kc.Index)
			authv2.POST("/keys/"+keys.path, auth.RequiresEditRole(keys.kc.Create))
			authv2.DELETE("/keys/"+keys.path+"/:keyID", auth.RequiresAdminRole(keys.kc.Delete))
			authv2.POST("/keys/"+keys.path+"/import", auth.RequiresAdminRole(keys.kc.Import))
			authv2.POST("/keys/"+keys.path+"/export/:ID", auth.RequiresAdminRole(keys.kc.Export))
		}

		vrfkc := VRFKeysController{app}
		authv2.GET("/keys/vrf", vrfkc.Index)
		authv2.POST("/keys/vrf", auth.RequiresEditRole(vrfkc.Create))
		authv2.DELETE("/keys/vrf/:keyID", auth.RequiresAdminRole(vrfkc.Delete))
		authv2.POST("/keys/vrf/import", auth.RequiresAdminRole(vrfkc.Import))
		authv2.POST("/keys/vrf/export/:keyID", auth.RequiresAdminRole(vrfkc.Export))

		wfkc := WorkflowKeysController{app}
		authv2.GET("/keys/workflow", wfkc.Index)

		dkrkc := DKGRecipientKeysController{app}
		authv2.GET("/keys/dkgrecipient", dkrkc.Index)

		jc := JobsController{app}
		authv2.GET("/jobs", paginatedRequest(jc.Index))
		authv2.GET("/jobs/:ID", jc.Show)
		authv2.POST("/jobs", auth.RequiresEditRole(jc.Create))
		authv2.PUT("/jobs/:ID", auth.RequiresEditRole(jc.Update))
		authv2.DELETE("/jobs/:ID", auth.RequiresEditRole(jc.Delete))

		// PipelineRunsController
		authv2.GET("/pipeline/runs", paginatedRequest(prc.Index))
		authv2.GET("/jobs/:ID/runs", paginatedRequest(prc.Index))
		authv2.GET("/jobs/:ID/runs/:runID", prc.Show)

		// FeaturesController
		fc := FeaturesController{app}
		authv2.GET("/features", fc.Index)

		// PipelineJobSpecErrorsController
		authv2.DELETE("/pipeline/job_spec_errors/:ID", auth.RequiresEditRole(psec.Destroy))

		lgc := LogController{app}
		authv2.GET("/log", lgc.Get)
		authv2.PATCH("/log", auth.RequiresAdminRole(lgc.Patch))

		chains := authv2.Group("chains")
		chainController := NewChainsController(
			app.GetRelayers(),
			app.GetLogger(),
			app.GetAuditLogger(),
		)
		chains.GET("", paginatedRequest(chainController.Index))
		chains.GET("/:network", paginatedRequest(chainController.Index))
		chains.GET("/:network/:ID", chainController.Show)

		nodes := authv2.Group("nodes")
		nodesController := NewNodesController(
			app.GetRelayers(),
			app.GetAuditLogger(),
		)
		nodes.GET("", paginatedRequest(nodesController.Index))
		nodes.GET("/:network", paginatedRequest(nodesController.Index))
		chains.GET("/:network/:ID/nodes", paginatedRequest(nodesController.Index))

		efc := EVMForwardersController{app}
		authv2.GET("/nodes/evm/forwarders", paginatedRequest(efc.Index))
		authv2.POST("/nodes/evm/forwarders/track", auth.RequiresEditRole(efc.Track))
		authv2.DELETE("/nodes/evm/forwarders/:fwdID", auth.RequiresEditRole(efc.Delete))

		buildInfo := BuildInfoController{app}
		authv2.GET("/build_info", buildInfo.Show)

		vault := VaultController{app}
		authv2.POST("/vault/dkg_results/verify", auth.RequiresEditRole(vault.VerifyDKGResult))
		authv2.POST("/vault/dkg_results/export", auth.RequiresEditRole(vault.ExportDKGResult))
```

**File:** core/web/auth/auth.go (L157-175)
```go
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

**File:** core/web/auth/auth.go (L200-254)
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
```
