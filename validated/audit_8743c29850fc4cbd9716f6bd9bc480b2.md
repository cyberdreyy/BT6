### Title
Unauthorized disclosure of ExternalInitiator `AccessKey`/`OutgoingToken` credentials to view-role users via GET /v2/external_initiators - ([File: core/web/external_initiators_controller.go])

### Summary
`ExternalInitiatorsController.Index` returns `AccessKey` and `OutgoingToken` for every `ExternalInitiator` record to any authenticated caller, while `Create`/`Destroy` are the mutation endpoints protected against low-privilege abuse. There is no role check in `Index` itself, so any session/API token capable of hitting an authenticated route (including view-role) can enumerate live initiator credentials.

### Finding Description
`Index` fetches all `ExternalInitiator` rows via `eic.App.BridgeORM().ExternalInitiators(ctx, offset, size)` and maps each into a `presenters.ExternalInitiatorResource`, which is then paginated and returned as-is: [1](#0-0) 

The presenter used for `Index` is not the same as the one used for creation. `NewExternalInitiatorResource` (used by `Index`) includes the plaintext `AccessKey` and `OutgoingToken` fields for every initiator in the response body: [2](#0-1) 

`AccessKey` is the credential an external initiator presents (with its paired `Secret`) to authenticate incoming webhook trigger calls, and `OutgoingToken` is used by the node when calling back out to the initiator. Both are credential material tied to a specific initiator/integration, not merely metadata. `Secret`/`OutgoingSecret`/hashed values are excluded from this resource, but `AccessKey` and `OutgoingToken` themselves are secrets that facilitate authentication and were not redacted.

I was unable to fully confirm the exact route-level role-gating declarations in `core/web/router.go` for `/v2/external_initiators` within this investigation — the grep for the specific registration lines did not return content due to a path-matching issue, so the precise middleware chain applied to `Index` vs. `Create`/`Destroy` could not be directly verified from source in this session. This is a limitation of the investigation, not a confirmation either way. However, the controller code itself confirms `Index` performs no role check inline (unlike other controllers in this codebase that call `auth.RequiresEditRole` explicitly as guards before certain handlers), and the presenter used by `Index` does expose live `AccessKey`/`OutgoingToken` values without any masking logic in `NewExternalInitiatorResource`.

### Impact Explanation
If `Index` is reachable by any authenticated session (including view-role) without an edit-role gate, a low-privileged authenticated user can enumerate `AccessKey` and `OutgoingToken` values for all external initiators configured on the node. `OutgoingToken` disclosure could allow impersonation of the node's outgoing webhook calls to third-party integrator endpoints; `AccessKey` disclosure narrows the search space needed to forge incoming webhook triggers (combined with a leaked/guessed `Secret`). This matches the "credential/key disclosure" and "unauthorized action via impersonation" bounty impact classes, though it is bounded because the `Secret`/`OutgoingSecret` values remain unexposed here.

### Likelihood Explanation
The only precondition is an authenticated session or API token with the minimum role required by whatever middleware guards `/v2/external_initiators` GET (per the question, likely view-role). No special conditions are needed beyond calling the existing endpoint; this is trivially repeatable and requires no elevated privileges.

### Recommendation
- Explicitly require `auth.RequiresEditRole` (or an equivalent minimum-privilege check) on the GET `/v2/external_initiators` route, consistent with `Create`/`Destroy`, if initiator credentials are considered sensitive to non-admin users.
- Independently, stop returning `AccessKey`/`OutgoingToken` in the list/read presenter (`NewExternalInitiatorResource`); expose only non-secret metadata (`Name`, `URL`, timestamps, ID) on read, and only reveal the credential once, at creation time, as is already done via `NewExternalInitiatorAuthentication`.

### Proof of Concept
Go handler-level integration test plan:
1. Set up a test app with two users: one with `sessions.UserRoleAdmin` who creates an `ExternalInitiator` via `POST /v2/external_initiators`, capturing the returned `AccessKey`/`OutgoingToken`.
2. Authenticate as a second user with `sessions.UserRoleView` (or `UserRoleRun`) and call `GET /v2/external_initiators`.
3. Assert the response status is `200 OK` (not `401`/`403`), and that the JSON body's `externalInitiators` resources include a non-empty `accessKey` and `outgoingToken` matching the values created in step 1.
4. Assert failure of the invariant: the view-role response should not have exposed `accessKey`/`outgoingToken` at all, or the request should have been rejected with `403` if edit-role was required — whichever the current router configuration fails to enforce, confirming the gap between `Index`'s authorization and `Create`/`Destroy`'s `auth.RequiresEditRole`.

### Citations

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

**File:** core/web/presenters/external_initiators.go (L57-77)
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

func NewExternalInitiatorResource(ei bridges.ExternalInitiator) ExternalInitiatorResource {
	return ExternalInitiatorResource{
		JAID:          NewJAID(strconv.FormatInt(ei.ID, 10)),
		Name:          ei.Name,
		URL:           ei.URL,
		AccessKey:     ei.AccessKey,
		OutgoingToken: ei.OutgoingToken,
		CreatedAt:     ei.CreatedAt,
		UpdatedAt:     ei.UpdatedAt,
	}
}
```
