### Title
View-role users can read node RPC URLs (including embedded provider API keys) via `GET /v2/nodes/:network` and `GET /v2/chains/:network/:ID/nodes` with no redaction in the response presenter - ([File: core/web/nodes_controller.go], [File: core/web/presenters/chain.go])

### Summary
`NodesController.Index` and `ChainsController.Show`'s companion nodes route both return `presenters.NodeResource.Config`, which is a raw TOML dump (`node.Config`) of the node's connection configuration, including `HTTPURL`/`WSURL` fields, with no masking of embedded credentials. These routes are explicitly marked `viewOnlyAllowed=true` in `core/web/auth/auth_test.go`, so a `UserRoleView` session can freely enumerate this data.

### Finding Description
The route table `core/web/router.go:414-431` registers:
```
nodes.GET("", paginatedRequest(nodesController.Index))
nodes.GET("/:network", paginatedRequest(nodesController.Index))
chains.GET("/:network/:ID/nodes", paginatedRequest(nodesController.Index))
```
all under `authv2` (session/token authenticated) with the default role gate that allows view role, matching `routesRolesMap` entries `{"GET", "/v2/nodes/evm", true, true, true}` and `{"GET", "/v2/chains/evm/MOCK/nodes", true, true, true}` in `core/web/auth/auth_test.go:327,331` (viewOnlyAllowed=true).

`NodesController.Index` (`core/web/nodes_controller.go:44-79`) fetches `types.NodeStatus` from relayers and converts each via `n.newResource(node)` → `presenters.NewNodeResource`. That function (`core/web/presenters/chain.go:39-47`) copies `node.Config` verbatim into `NodeResource.Config`, which is serialized to JSON via `jsonapi.Marshal` and returned to the caller (confirmed exact field mapping by `core/web/presenters/node_test.go:24-53`).

The `Config` string is produced by TOML-marshaling the node's connection struct (e.g., `evmtest.nodeStatus`: `b, err := toml.Marshal(n); s.Config = string(b)` at `core/internal/testutils/evmtest/evmtest.go:286-296`), and elsewhere in the resolver test fixtures the same raw pattern is used: `Config: "Name='node-name'\nOrder=11\nHTTPURL='http://some-url'\nWSURL='ws://some-url'"` (`core/web/resolver/node_test.go:48`). The `HTTPURL`/`WSURL` fields on `configtoml.Node` are plain `*config.URL`, not `config.SecretURL`/`models.SecretURL` (the repo's redaction mechanism defined in `core/store/models/secrets.go:14-19`, which formats as `xxxxx` when marshaled). Because the node URL type is not wrapped in `SecretURL`, `toml.Marshal` emits the full URL string as-is, including any query-string API keys (e.g., `?apikey=SECRET`) that operators may have embedded per common Infura/Alchemy usage patterns.

No redaction step exists between `NodeStatus.Config` and the JSON response in `NewNodeResource`, `NodesController.Index`, or the GraphQL equivalent (`core/web/resolver/node.go` unmarshal/re-expose of `httpURL`/`wsURL` fields, confirmed exposed directly in `core/web/resolver/node_test.go:104-151`).

### Impact Explanation
A `UserRoleView` (view-only, lowest privileged authenticated role) session can call `GET /v2/nodes/evm` or `GET /v2/chains/evm/:ID/nodes` and receive the full node TOML config in the `config` JSON attribute, including RPC endpoint URLs. If an operator's HTTPURL/WSURL contains an embedded provider API key (a common real-world practice for Infura/Alchemy/QuickNode), that secret is disclosed to a low-privileged user who should only have read access to non-sensitive status information. This matches a "sensitive credential/secret disclosure to unauthorized/low-privileged principal" impact class.

### Likelihood Explanation
Feasible and repeatable with only a valid `UserRoleView` session — no additional bypass is required since the routes are intentionally allowed for view role. The only precondition affecting exploitability is whether the operator has actually embedded an API key in the URL string rather than configuring it via a separate secrets field; the vulnerability is the lack of any redaction guarantee in the presenter/config marshaling path, independent of whether every deployment is affected.

### Recommendation
Wrap node connection URL fields (`HTTPURL`, `WSURL`, and any send-only URLs) in the existing `config.SecretURL`/`models.SecretURL` type so that TOML marshaling redacts them by default (consistent with how `chainlink.Secrets.TOMLString()` already redacts credentials at `core/services/chainlink/config.go:448-455`), or explicitly strip/mask query parameters and userinfo from `NodeStatus.Config` before constructing `presenters.NodeResource` in `NewNodeResource`. Alternatively, restrict `Config`/URL exposure in `NodesController.Index` responses to admin/edit roles only, or return a scrubbed subset (e.g., host without query string) for view-role requests.

### Proof of Concept
Go unit test plan (presenter-level, extendable to handler-level integration):
1. Build a `types.NodeStatus{ChainID: "1", Name: "node1", State: "alive", Config: "Name='node1'\nHTTPURL='https://mainnet.infura.io/v3/?apikey=SUPERSECRET'\nWSURL='wss://mainnet.infura.io/ws/?apikey=SUPERSECRET'"}` mimicking real TOML output from `configtoml.Node` marshaling.
2. Call `presenters.NewNodeResource(nodeStatus)` and `jsonapi.Marshal` it (as done in `core/web/presenters/node_test.go`).
3. Assert the JSON `attributes.config` string does NOT contain `"SUPERSECRET"` — current behavior will fail this assertion, proving the leak.
4. Handler-level integration test extension: spin up `cltest.NewApplicationEVMDisabled`, seed an EVM chain/node config containing an API-key-bearing URL, create a `UserRoleView` session via `cltest.User{Role: sessions.UserRoleView}` and `app.NewHTTPClient(u)`, `GET /v2/nodes/evm`, parse JSON body, and assert the API key substring is absent from the response — reproducing the pattern used in `TestRBAC_Routemap_ViewOnly` (`core/web/auth/auth_test.go:484-532`) but adding a body-content secret-leak assertion instead of only checking status codes.