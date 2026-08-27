### Title
View-role users can read the node's live `OutgoingToken` via `GET /v2/external_initiators`, enabling impersonation of the node's outgoing EI webhook calls - ([File: core/web/presenters/external_initiators.go])

### Finding Description
`ExternalInitiatorsController.Index` (`core/web/external_initiators_controller.go` lines 50-59) lists all external initiators and serializes each one with `presenters.NewExternalInitiatorResource` (`core/web/presenters/external_initiators.go` lines 57-77). That resource type intentionally excludes the *incoming* `Secret` (used by the EI to authenticate to the node) but explicitly includes `AccessKey` and, critically, `OutgoingToken`:

```go
type ExternalInitiatorResource struct {
	JAID
	Name          string         `json:"name"`
	URL           *models.WebURL `json:"url"`
	AccessKey     string         `json:"accessKey"`
	OutgoingToken string         `json:"outgoingToken"`
	...
}
``` [1](#0-0) 

`OutgoingToken` (together with `OutgoingSecret`, stored on the `bridges.ExternalInitiator` model) is the credential the node itself presents when it calls back out to the external initiator's webhook URL — i.e., it is the node's outbound identity toward that third party, not a value that should ever be returned to an API caller other than at creation time (compare `NewExternalInitiatorAuthentication`, which is only invoked from `Create` and returns both `Secret` and `OutgoingSecret` once, at creation, per lines 22-38 and 98 of the two files respectively). The `Index` handler unconditionally returns `OutgoingToken` in the JSON:API payload for every existing EI record on every subsequent `GET /v2/external_initiators` call. [2](#0-1) 

I attempted to confirm the exact role requirement gating this route by locating the `v2Routes` function that wires `ExternalInitiatorsController` into the router, but was unable to retrieve its body within the available tool budget (only the top-level `NewRouter` in `core/web/router.go` was inspected, which delegates to `v2Routes(app, api)` without showing the per-route role middleware). I could not verify from the retrieved code whether the `Index` route is admin-only, edit-role-only, or accessible to any authenticated (including view-role) session. This is a materially load-bearing fact for the reported vulnerability: if the route is restricted to admin/edit roles, the "view-role escalation" premise of the question is not exploitable, and only the general secret-hygiene issue (returning a long-lived outgoing credential on every list call to any permitted caller) would remain.

### Impact Explanation
If the `Index` route is reachable by view-role sessions (unconfirmed from available code), an unprivileged view-role user could enumerate all external initiators and obtain each one's `OutgoingToken`, which is the credential the node uses to authenticate itself to that external initiator's URL. This would let the attacker impersonate the node's outgoing calls to the third-party EI endpoint — a request/identity impersonation issue. Even absent role bypass, exposing `OutgoingToken` on every `Index`/list call (rather than only once at creation) is a secret-confinement violation, since the value is a durable, reusable credential rather than a one-time secret display.

### Likelihood Explanation
Preconditions: an authenticated session with at least the minimum role permitted on `GET /v2/external_initiators`, and at least one external initiator configured on the node. Whether that minimum role is "view" could not be confirmed from the retrieved router code, so likelihood cannot be assessed with certainty. If the route is admin/edit-only, likelihood of the specific view-role escalation scenario is low/not applicable; if it is open to any authenticated role, likelihood is high and trivially repeatable (single GET request).

### Recommendation
- Ensure `ExternalInitiatorResource` (used for list/index responses) never returns `OutgoingToken`/`OutgoingSecret`; only return these once, at creation, via `ExternalInitiatorAuthentication`.
- Explicitly restrict `GET /v2/external_initiators` to admin (or at least edit) role if it currently permits view-role access.
- Verify and, if needed, add an integration test asserting `OutgoingToken` is absent from `Index` responses for all roles.

### Proof of Concept
1. In `core/web/external_initiators_controller_test.go`, add a test that creates an EI as admin, then issues `GET /v2/external_initiators?page=1` using a view-role (and separately edit-role) authenticated client.
2. Assert the HTTP status: if not admin-permitted, expect 401/403.
3. If the request succeeds, parse the JSON:API response and assert `data[].attributes.outgoingToken` is empty/absent, matching the intentional omission already applied to `incomingSecret`.
4. If `outgoingToken` is present in a view-role response, the test fails, confirming the leak; if the route itself rejects view-role callers, the escalation path is closed and only the "returned on every Index call" secret-hygiene concern remains, without a role-bypass component.

**Caveat**: Due to incomplete visibility into the `v2Routes` role-middleware wiring for `/v2/external_initiators`, this report cannot definitively confirm the "view-role user" precondition is currently exploitable; a Devin session with full repo access should verify the exact middleware/role applied to this route before treating this as a confirmed, exploitable finding rather than a defense-in-depth/secret-hygiene issue.

### Citations

**File:** core/web/presenters/external_initiators.go (L57-65)
```go
type ExternalInitiatorResource struct {
	JAID
	Name          string         `json:"name"`
	URL           *models.WebURL `json:"url"`
	AccessKey     string         `json:"accessKey"`
	OutgoingToken string         `json:"outgoingToken"`
	CreatedAt     time.Time      `json:"createdAt"`
	UpdatedAt     time.Time      `json:"updatedAt"`
}
```

**File:** core/web/external_initiators_controller.go (L50-59)
```go
func (eic *ExternalInitiatorsController) Index(c *gin.Context, size, page, offset int) {
	ctx := c.Request.Context()
	externalInitiators, count, err := eic.App.BridgeORM().ExternalInitiators(ctx, offset, size)
	resources := make([]presenters.ExternalInitiatorResource, 0, len(externalInitiators))
	for _, initiator := range externalInitiators {
		resources = append(resources, presenters.NewExternalInitiatorResource(initiator))
	}

	paginatedResponse(c, "externalInitiators", size, page, resources, count, err)
}
```
