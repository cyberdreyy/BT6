### Title
View-role users can enumerate RPC node URLs/credentials via unguarded `GET /v2/nodes` and `/v2/chains/:network/:ID/nodes` - ([File: core/web/router.go])

### Finding Description
In `v2Routes`, every other sensitive endpoint in the file is explicitly wrapped with a role guard such as `auth.RequiresEditRole` or `auth.RequiresAdminRole` (e.g. keys, jobs, transfers, forwarders). The nodes routes are the outlier — they are registered with only the generic `paginatedRequest` wrapper and no role check at all: [1](#0-0) 

These routes sit inside the `authv2` group, which only requires a valid authenticated session or API token (`auth.Authenticate(...)`), not any particular role: [2](#0-1) 

`nodesController.Index` calls `relayers.NodeStatuses(...)` and wraps each `types.NodeStatus` in a presenter via `n.newResource(node)` (`presenters.NewNodeResource`), then returns it directly in the paginated JSON:API response with no field-level redaction or role-based filtering: [3](#0-2) 

`types.NodeStatus` (from `chainlink-common/pkg/types`) carries a `Config` field containing the raw per-node relayer configuration (TOML), which for EVM/Solana/Cosmos nodes includes the actual `WSURL`/`HTTPURL` connection strings — these frequently embed provider API keys as URL path segments or query parameters (e.g., Infura/Alchemy-style URLs). `presenters.NewNodeResource` passes this content through into the response body without redacting the URL or credential portion, so any authenticated caller — including one holding only a view-role session or view-scoped API token — receives the full RPC endpoint strings for every configured chain/node.

Because the route lacks a role check, `auth.Authenticate` is the only gate, and it accepts any valid role (view, run, edit, admin). This is a real authorization gap: sibling read endpoints for comparably sensitive material (e.g., ETH/OCR/CSA key export, EVM forwarders track) are gated by `RequiresEditRole`/`RequiresAdminRole`, but nodes' listing endpoint — which discloses connection secrets — is not gated at all.

### Impact Explanation
A view-role (lowest-privilege) authenticated user can call `GET /v2/nodes` or `GET /v2/chains/:network/:ID/nodes` and obtain every configured node's RPC endpoint URL across all chains, including any API keys embedded in those URLs. This matches the Chainlink bounty "sensitive data / secret disclosure" impact class: exposure of internal/private RPC provider credentials to a low-privilege principal, which can then be used to exhaust the operator's RPC provider quota, incur costs, or pivot to further reconnaissance against the node's infrastructure.

### Likelihood Explanation
Preconditions are minimal: any valid authenticated session or API token with `view` role (the default/lowest role assignable in Chainlink's user model) is sufficient. No special timing, race condition, or complex chaining is required — a single unauthenticated-role-checked GET request against a route that is already registered and reachable. This is fully repeatable and deterministic.

### Recommendation
Wrap the nodes listing routes with an explicit role guard consistent with other sensitive read endpoints (at minimum `auth.RequiresEditRole`, or introduce field-level redaction in `presenters.NewNodeResource` that strips credentials/query parameters from `Config`/URL fields for callers below edit role). Additionally, audit `presenters.NewNodeResource` to redact secrets (API keys, credentials) from the `Config`/URL fields regardless of caller role, since defense-in-depth redaction protects even authorized-but-lower-trust callers (e.g., external log viewers, audit exports).

### Proof of Concept
1. In `core/web/nodes_controller_test.go` (or a new test), set up a `NodesController` backed by a mock `RelayerChainInteroperators` whose `NodeStatuses` returns a `types.NodeStatus` with `Config` containing a URL with embedded credentials, e.g. `wsUrl = "wss://mainnet.infura.io/ws/v3/SECRET_API_KEY"`.
2. Build a test router using `v2Routes`/`NewRouter` with a session/token authenticated as `view` role only (no edit/admin claims).
3. Issue `GET /v2/nodes`.
4. Assert response status is `200` (confirming no role gate blocks it) and that the JSON:API payload's node resource contains the unredacted `SECRET_API_KEY` string — demonstrating the leak — instead of a redacted/masked value.
5. Add a companion assertion test proving that other comparable endpoints (e.g., `/v2/keys/eth/export/:address`) correctly reject the same view-role token with `403`, highlighting the inconsistency in the nodes route's authorization.

### Citations

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L424-431)
```go
		nodes := authv2.Group("nodes")
		nodesController := NewNodesController(
			app.GetRelayers(),
			app.GetAuditLogger(),
		)
		nodes.GET("", paginatedRequest(nodesController.Index))
		nodes.GET("/:network", paginatedRequest(nodesController.Index))
		chains.GET("/:network/:ID/nodes", paginatedRequest(nodesController.Index))
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
