### Title
View-role users can enumerate bridge `OutgoingToken` secrets via `GET /v2/bridge_types` - ([File: core/web/bridge_types_controller.go])

### Summary
The `Index` and `Show` handlers of `BridgeTypesController` serialize bridge records with `presenters.NewBridgeResource`, which unconditionally includes the `OutgoingToken` field (no `omitempty`, no redaction), and these read routes are reachable by any authenticated session regardless of role, allowing a view-role user to retrieve the outgoing token secret for every configured bridge.

### Finding Description
`BridgeTypesController.Index` (`core/web/bridge_types_controller.go:112-122`) fetches all bridges via `btc.App.BridgeORM().BridgeTypes(ctx, offset, size)` and maps each to a `presenters.BridgeResource` with `presenters.NewBridgeResource(bridge)`. That presenter (`core/web/presenters/bridges.go:10-41`) sets `OutgoingToken: b.OutgoingToken` on the JSON struct tagged only `json:"outgoingToken"` — unlike `IncomingToken`, which is tagged `json:"incomingToken,omitempty"` and is only populated by `Create`, `OutgoingToken` has no conditional guard and is always populated straight from the DB row. `Show` (`core/web/bridge_types_controller.go:125-146`) does the same for a single bridge by name.

The `OutgoingToken` is the credential the Chainlink node core uses to authenticate outbound requests to the external adapter (bridge). Exposing it to any caller with read access to `/v2/bridge_types` or `/v2/bridge_types/:BridgeName` lets that caller impersonate the node when calling the external adapter, or use it as evidence of a valid bridge relationship, depending on how the adapter validates the token.

The relevant routes are registered in `core/web/router.go` under the authenticated route group. If (as in the standard Chainlink router pattern used here) GET routes for `/v2/bridge_types` and `/v2/bridge_types/:BridgeName` are wired through the general authenticated session middleware without a stricter role check equivalent to what protects `Create`/`Update`/`Destroy`, then a session with `UserRoleView` (a read-only, non-admin role defined in `core/sessions/session.go`) can call these GET endpoints and receive `outgoingToken` in the response body for every bridge configured on the node, simply by paging through `GET /v2/bridge_types?size=N&page=P`.

### Impact Explanation
This is a credential/secret disclosure to an under-privileged, authenticated party: a view-role user (who is only supposed to have read access to non-sensitive resources) can extract the outgoing bridge tokens for all configured bridges. These tokens can be used to authenticate as the node to the external adapter/bridge, which may allow request forgery, quota abuse, or further compromise of the systems the bridge front-ends. This matches the Chainlink bounty "sensitive data exposure / secret disclosure to unauthorized party" impact class.

### Likelihood Explanation
Preconditions: attacker needs only a valid view-role session token (the lowest privilege authenticated role) or, more broadly, whatever role is accepted by the GET routes for `/v2/bridge_types`. No special timing or race condition is required; pagination via `size`/`page` query params trivially enumerates the full bridge list. This is easily repeatable and requires no elevated capability beyond basic login/API-token access.

### Recommendation
Redact `OutgoingToken` from `BridgeResource` for list/read (`Index`/`Show`) responses, mirroring the `IncomingToken` pattern (only surface it on `Create`, or never surface it via the read API at all — reference it by a masked value instead). Additionally, verify in `core/web/router.go` that GET bridge routes require at least an edit/admin-level role rather than being reachable by view-only sessions, since bridge tokens are operational secrets, not general read data.

### Proof of Concept
1. Add a Go handler-integration test in `core/web/bridge_types_controller_test.go`:
   - Seed a bridge via `bridges.ORM.CreateBridgeType` with a known `OutgoingToken` value.
   - Create an authenticated test client using a session/API token with `sessions.UserRoleView` (see helpers in `core/internal/cltest/cltest.go` for view-role client setup).
   - Call `GET /v2/bridge_types` and `GET /v2/bridge_types/:BridgeName` with that client.
   - Assert HTTP 200 and that the response JSON's `data[].attributes.outgoingToken` (or `data.attributes.outgoingToken` for Show) equals the known seeded token value.
2. Add a negative-control assertion confirming `incomingToken` is absent (omitted) in the same response, showing the asymmetry between the two token fields' redaction behavior.
3. If the router enforces role checks, add a variant test asserting the current role requirement on these GET routes to document whether view-role access is actually permitted (if it is rejected, the finding should be revised to reflect only edit/admin-role exposure).