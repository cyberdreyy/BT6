### Title
View-role users can enumerate node RPC URLs (including embedded API keys) via unrestricted `/v2/nodes` and `/v2/chains` endpoints - ([File: core/web/router.go])

### Summary
The `chains` and `nodes` route groups registered in `v2Routes` in `core/web/router.go` are only wrapped in `auth.Authenticate` (session/token) but have no `auth.RequiresEditRole`/`auth.RequiresAdminRole` wrapper, unlike nearly every other sensitive route in the same function. The underlying `NodeResource`/`ChainResource` presenters return the node's full raw TOML `Config` string, which includes `HTTPURL`/`WSURL` fields unredacted, so any authenticated view-role user can retrieve RPC endpoint URLs that may embed provider API keys.

### Finding Description
In `core/web/router.go`, the `chains` and `nodes` groups are created from `authv2` (session/token authenticated) but registered without any role wrapper: [1](#0-0) 

Compare this to virtually all other mutating and even some read routes in the same function, which are wrapped with `auth.RequiresEditRole` or `auth.RequiresAdminRole` (e.g. `authv2.GET("/keys/eth", ...)` has no wrapper either, but sensitive config/secret-adjacent reads like `/keys/.../export` do use `RequiresAdminRole`). For chains/nodes there is no role gate at all, so `UserRoleView` (the lowest privilege authenticated role) can hit these routes.

The handler `nodesController.Index` in `core/web/nodes_controller.go` fetches `types.NodeStatus` from the relayers and converts to `presenters.NodeResource` via `NewNodeResource`, which copies `node.Config` (a TOML string) verbatim into the JSON response: [2](#0-1) 

This `Config` string is generated from the node's TOML configuration and includes the raw `HTTPURL`/`WSURL` values, as confirmed by the resolver equivalent and tests, e.g.: [3](#0-2) 
and the EVM node TOML fields (`HTTPURL`, `WSURL`) are plain `config.URL` (not `SecretURL`), so `toml.Marshal` emits them in full including any query-string API keys: [4](#0-3) 

Because no role check gates `chains.GET("")`, `chains.GET("/:network")`, `chains.GET("/:network/:ID")`, or `nodes.GET("")`, a `UserRoleView` session/token — the minimum privileged authenticated principal — can call these endpoints and receive the full node `Config`, including any embedded RPC provider API key in the URL. No existing redaction mechanism (like the `SecretURL`/`SecretString` types used elsewhere, e.g. `core/store/models/secrets.go`) is applied to node URLs before they reach the presenter/JSON response.

### Impact Explanation
A low-privilege view-role authenticated user (or any user issued a restricted view-role API token) can read RPC endpoint URLs of all configured chains/nodes, including provider API keys embedded in query strings (e.g., Infura/Alchemy-style URLs). This is a credential/secret exposure that lets an unprivileged principal exfiltrate third-party RPC provider credentials, enabling quota theft, cost abuse, or further reconnaissance against the operator's infrastructure — matching the "sensitive data / secret exposure" bounty class rather than a role/authorization bypass (since authentication is still required, but the role check that should differentiate view vs. edit/admin is entirely absent for these routes).

### Likelihood Explanation
The only precondition is possessing valid `UserRoleView` credentials (session cookie or restricted API token) — the lowest role tier explicitly designed to have read-only, non-sensitive access. No further exploitation steps, timing, or race conditions are required; a single `GET /v2/nodes` or `GET /v2/chains/:network/:ID` call fully discloses the configured RPC URL. This is trivially repeatable and always succeeds if the node config contains a URL-embedded key.

### Recommendation
Wrap the `chains` and `nodes` GET routes with an appropriate role wrapper (at minimum `auth.RequiresEditRole`, consistent with the sensitivity of RPC credentials), and/or redact the `HTTPURL`/`WSURL` fields (or entire `Config` string) in `presenters.NewNodeResource`/`NewChainResource` before serialization — e.g., convert node URLs to a `SecretURL`-like type that renders as `"xxxxx"` in TOML/JSON output for non-admin roles.

### Proof of Concept
1. In `core/web/nodes_controller_test.go` (or an equivalent handler-integration test), set up a `TestApplication`/relayer config with an EVM node whose `HTTPURL` contains a query-string API key, e.g. `https://mainnet.infura.io/v3/SECRET_API_KEY`.
2. Create a session/token for a user with `sessions.UserRoleView`.
3. Issue `GET /v2/nodes` (and `GET /v2/chains/evm/:chainID`) authenticated as the view-role user.
4. Assert HTTP 200 (not 401/403) is returned.
5. Assert the JSON response's `attributes.config` contains the literal string `SECRET_API_KEY`, proving disclosure to a view-role principal.
6. Expected (fixed) behavior: either the request is rejected with 403 for view role, or the returned `config` has the URL redacted (e.g., `HTTPURL = 'xxxxx'`).

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

**File:** core/web/presenters/chain.go (L30-47)
```go
type NodeResource struct {
	JAID
	ChainID string `json:"chainID"`
	Name    string `json:"name"`
	Config  string `json:"config"` // TOML
	State   string `json:"state"`
}

// NewNodeResource returns a new NodeResource for node.
func NewNodeResource(node types.NodeStatus) NodeResource {
	return NodeResource{
		JAID:    NewPrefixedJAID(node.Name, node.ChainID),
		ChainID: node.ChainID,
		Name:    node.Name,
		State:   node.State,
		Config:  node.Config,
	}
}
```

**File:** core/web/resolver/node_test.go (L44-51)
```go
					Nodes: []types.NodeStatus{
						{
							ChainID: "1",
							Name:    "node-name",
							Config:  "Name='node-name'\nOrder=11\nHTTPURL='http://some-url'\nWSURL='ws://some-url'",
							State:   "alive",
						},
					},
```

**File:** core/services/chainlink/config_test.go (L1282-1293)
```go
HTTPURL = 'https://foo.web'
HTTPURLExtraWrite = 'https://foo.web/extra'

[[EVM.Nodes]]
Name = 'bar'
WSURL = 'wss://web.socket/test/bar'
HTTPURL = 'https://bar.com'

[[EVM.Nodes]]
Name = 'broadcast'
HTTPURL = 'http://broadcast.mirror'
SendOnly = true
```
