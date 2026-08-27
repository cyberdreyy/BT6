### Title
View-role users can enumerate node configurations via unrestricted `chains.GET`/`nodes.GET` routes - ([File: core/web/router.go])

### Summary
The `/v2/chains*` and `/v2/nodes*` routes, including `GET /v2/chains/evm/:network/:ID/nodes`, are registered under the authenticated `authv2` group but are wrapped only with `paginatedRequest`, with no `auth.RequiresEditRole` or `auth.RequiresAdminRole` wrapper as is used for every other sensitive resource in the same file. Any authenticated user, including one with the lowest `UserRoleView` role, can therefore call these endpoints and receive the full node/chain configuration payload produced by `NodesController.Index` / `ChainsController.Index`.

### Finding Description
In `core/web/router.go`, the chains/nodes routes are defined as: [1](#0-0) 

Unlike nearly every other write-adjacent or config-adjacent route in the same function (`jobs`, `keys/*`, `bridge_types`, `external_initiators`, `vault`, etc.), which are wrapped in `auth.RequiresEditRole(...)` or `auth.RequiresAdminRole(...)`, the `chains.GET(...)` and `nodes.GET(...)` handlers are only wrapped by `paginatedRequest`, which does not perform any role check — it only parses pagination query params. The only gate present is the outer `authv2` group's `auth.Authenticate(...)` middleware, which accepts session or token authentication for a user of *any* role (view, run, edit, admin), as seen in `auth.RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` in `core/web/auth/auth.go`, which explicitly checks `user.Role == clsessions.UserRoleView` (or `Run`) to reject lower-privileged users — logic that is entirely absent for the chains/nodes routes. [2](#0-1) 

This means a request such as `GET /v2/chains/evm/:network/:ID/nodes` reaches `nodesController.Index` in `core/web/nodes_controller.go`, which fetches `types.NodeStatus` records via `relayers.NodeStatuses` and converts each into a presenter resource via `n.newResource(node)` (i.e., `presenters.NewNodeResource`), returning it to the caller with no additional authorization check inside the controller itself: [3](#0-2) 

I was unable to locate and inspect the body of `presenters.NewNodeResource` (only test references were found in the index, not the presenter source itself), so I cannot confirm from code whether the presenter redacts credential-bearing query strings or path segments from node RPC URLs before serialization. This is a gap in what I could verify — it's possible the presenter does perform redaction, which would reduce (but not eliminate) the severity of the missing role check, since other non-URL configuration details (chain IDs, node names, relay configuration) would still be exposed to view-role users who should not normally see them per the principle of least privilege embedded in the rest of the route table.

### Impact Explanation
Regardless of the exact contents redacted by the presenter, this is a role/authorization-bypass finding: the intended access-control model in this codebase (visible via the consistent `RequiresEditRole`/`RequiresAdminRole` wrapping pattern for every other API-key/config-adjacent resource) is not applied to chain/node configuration endpoints. A view-role user — the lowest privilege authenticated principal — can read full node/chain state that the routing design elsewhere treats as requiring elevated privilege. If the RPC URLs stored in node configs are not redacted by the presenter (unverified due to missing access to `presenters.NewNodeResource` source), this constitutes direct secret/credential disclosure (embedded API keys in RPC provider URLs) to an unprivileged authenticated user, matching a "sensitive data exposure" / "unauthorized information disclosure" bounty class.

### Likelihood Explanation
The only precondition is possessing valid view-role credentials, the lowest tier issued by admins via `POST /v2/users` (`auth.RequiresAdminRole(uc.Create)`), which are commonly distributed for read-only dashboard access. Exploitation is a single unauthenticated-by-role GET request — trivial and fully repeatable.

### Recommendation
Wrap `chains.GET(...)`/`nodes.GET(...)` routes with `auth.RequiresEditRole` (or a new `auth.RequiresViewRole`-equivalent gate consistent with intended exposure) to match the access-control pattern used for every other configuration resource in `core/web/router.go`. Additionally, verify/ensure `presenters.NewNodeResource` redacts any credential material embedded in node URLs before serialization, independent of the role fix.

### Proof of Concept
1. In `core/web/chains_controller_integration_test.go` / `core/web/nodes_controller_test.go` style, create an app with a chain configured with a node whose `WSURL`/`HTTPURL` contains a query-string API key (e.g., `https://rpc.example.com/?apikey=SECRET123`).
2. Create a session/token for a user with `clsessions.UserRoleView`.
3. Issue `GET /v2/chains/evm/:network/:ID/nodes` authenticated as that view-role user.
4. Assert: request succeeds with `200 OK` (no `401`/`403`), proving the missing role wrapper.
5. Inspect the JSON:API response body; assert whether the node URL fields contain the literal `SECRET123` substring — if present, this confirms full credential disclosure to a view-role user in addition to the authorization-bypass on the route itself.

### Citations

**File:** core/web/router.go (L414-431)
```go
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

**File:** core/web/nodes_controller.go (L44-79)
```go
func (n *nodesController[R]) Index(c *gin.Context, size, page, offset int) {
	id := c.Param("ID")
	network := c.Param("network")

	var nodes []types.NodeStatus
	var count int
	var err error

	relayers := n.relayers
	if network != "" {
		relayers = relayers.List(chainlink.FilterRelayersByType(network))
	}

	ctx := c.Request.Context()
	if id == "" {
		// fetch all nodes
		nodes, count, err = relayers.NodeStatuses(ctx, offset, size)
	} else {
		// fetch nodes for chain ID
		// backward compatibility
		var rid types.RelayID
		err = rid.UnmarshalString(id)
		if err != nil {
			rid.ChainID = id
			rid.Network = network
		}
		nodes, count, err = relayers.NodeStatuses(ctx, offset, size, rid)
	}

	resources := make([]R, 0, len(nodes))
	for _, node := range nodes {
		res := n.newResource(node)
		resources = append(resources, res)
	}

	paginatedResponse(c, "node", size, page, resources, count, err)
```
