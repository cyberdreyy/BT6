### Title
Unauthorized disclosure of external-initiator AccessKey and OutgoingToken to view-role sessions via GET /v2/external_initiators - ([File: core/web/external_initiators_controller.go])

### Summary
`ExternalInitiatorsController.Index` serializes every external initiator's `AccessKey` and `OutgoingToken` fields into the JSON:API response via `presenters.NewExternalInitiatorResource`, and the `GET /v2/external_initiators` route is wired without an edit/run-role gate while `Create`/`Destroy` are edit-role gated. This lets any authenticated view-role session enumerate active external-initiator credentials.

### Finding Description
`ExternalInitiatorsController.Index` (`core/web/external_initiators_controller.go`) calls `eic.App.BridgeORM().ExternalInitiators(ctx, offset, size)` and maps each record through `presenters.NewExternalInitiatorResource(initiator)` [1](#0-0) . That presenter type includes `AccessKey` and `OutgoingToken` as plain, non-redacted JSON fields, unlike the `Create` path which only ever returns `ExternalInitiatorAuthentication` (containing the freshly generated `Secret`/`OutgoingSecret`) at creation time [2](#0-1) . `AccessKey` is the credential an external initiator presents to the node to authenticate its calls, and `OutgoingToken` is the credential the node presents to the external initiator when it calls back out for job runs — both are secrets whose exposure enables forging webhook requests. In the router, `POST /v2/external_initiators` (`Create`) and `DELETE /v2/external_initiators/:Name` (`Destroy`) are the only handlers gated behind `RequiresEditRole`, while `GET /v2/external_initiators` (`Index`, wrapped in `paginatedRequest`) has no such role check, so any authenticated session — including a `UserRoleView` session — can call it and receive the `accessKey`/`outgoingToken` fields for every stored external initiator.

### Impact Explanation
A low-privileged, view-only authenticated user can read `accessKey` and `outgoingToken` values for external initiators created by higher-privileged users, allowing them to impersonate the external initiator when calling into the node's job-run endpoints or to forge/replay outbound webhook authentication toward the initiator's own endpoint. This matches the Chainlink bounty impact class of unauthorized credential/secret disclosure via authorization bypass, scoped to low-privileged read access rather than full node compromise.

### Likelihood Explanation
The only precondition is a valid `UserRoleView` session/API token (the lowest privilege role) and knowledge that at least one external initiator exists. The request is a single unauthenticated-in-role, standard `GET /v2/external_initiators` call, fully reproducible and repeatable with no rate limiting or extra validation blocking it, since the route only requires general session authentication, not an edit/run role.

### Recommendation
Gate `GET /v2/external_initiators` (and any other external-initiator read endpoints) behind at least `RequiresEditRole` (or a new minimum "operate" role), and/or redact `AccessKey`/`OutgoingToken` from `ExternalInitiatorResource` for list/index responses, returning only metadata (name, URL, timestamps) unless the caller is privileged.

### Proof of Concept
1. As an edit/admin-role authenticated client, `POST /v2/external_initiators` with a name/url, capturing the created external initiator's name.
2. Authenticate a second session/token with role `UserRoleView`.
3. Issue `GET /v2/external_initiators` with the view-role session.
4. Assert HTTP 200 (not 401/403) and that the JSON:API `data[].attributes` payload contains non-empty `accessKey` and `outgoingToken` fields matching the values generated in step 1, proving credential disclosure to an under-privileged role — mirrored as a Go handler test extending `core/web/external_initiators_controller_test.go` with a `UserRoleView` client for the Index case.

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
