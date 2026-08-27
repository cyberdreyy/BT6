### Title
Any authenticated session (regardless of role) can enumerate plaintext `AccessKey`/`OutgoingToken` secrets for ALL external initiators via GET /v2/external_initiators - ([File: core/web/external_initiators_controller.go])

### Summary
`ExternalInitiatorsController.Index` returns every external initiator record in the node, including the sensitive `AccessKey` and `OutgoingToken` fields, without restricting the endpoint to `RequiresEditRole`/`RequiresAdminRole` and without any per-caller ownership filtering. Any session with the minimum authenticated role can call this route and harvest every other external initiator's outgoing credentials.

### Finding Description
`ExternalInitiatorsController.Index` calls `eic.App.BridgeORM().ExternalInitiators(ctx, offset, size)`, which lists all external initiators stored in the node (there is no per-user/tenant scoping concept for external initiators — they are node-wide resources), and converts each one with `presenters.NewExternalInitiatorResource(initiator)`. [1](#0-0) 

`presenters.NewExternalInitiatorResource` copies `ei.AccessKey` and `ei.OutgoingToken` directly into the JSON-serialized `ExternalInitiatorResource`, with no redaction: [2](#0-1) 

`AccessKey` and `OutgoingToken` are authentication secrets meant to identify/authorize the specific external initiator when calling back into the node (analogous to `Create`'s `presenters.NewExternalInitiatorAuthentication`, which is only returned once at creation time to the creator). Exposing them via `Index` to any caller who merely holds a valid, low-privilege session (per the router configuration referenced in the question, GET is not gated by `RequiresEditRole`/`RequiresAdminRole`) breaks the confinement of these secrets to the party that created the initiator, allowing a low-privileged/view-role user to read and reuse another party's `AccessKey`/`OutgoingToken` to impersonate that external initiator or forge outgoing webhook validations.

### Impact Explanation
This is a credential/secret-disclosure vulnerability: any authenticated caller — even one intended to have read-only/minimum privileges — can retrieve the full set of `AccessKey`/`OutgoingToken` values for every external initiator configured on the node, not just their own. These secrets can then be used to authenticate as that external initiator (`AccessKey`) or to construct valid outgoing-token-authenticated requests, enabling unauthorized job runs to be triggered or webhook responses to be forged by an unrelated low-privileged user.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs any valid authenticated session/API token on the node (the lowest role tier), and there must be at least one other external initiator configured. No admin/operator access, no host access, and no social engineering are required. The exploit is trivially repeatable — a single `GET /v2/external_initiators` call returns the full list with secrets in plaintext.

### Recommendation
- Redact `AccessKey` and `OutgoingToken` from `ExternalInitiatorResource`/`Index`'s response entirely (list endpoint should not need to re-expose secrets after creation), or
- Restrict the `GET /v2/external_initiators` route to `RequiresAdminRole` (or higher) as is done for other sensitive listing endpoints, and additionally strip the secret fields from the list response regardless of role, since even admins listing many initiators shouldn't need plaintext secrets replayed to the browser/log.

### Proof of Concept
Go handler-level integration test plan:
1. Set up an app via `cltest.NewApplicationWithConfig` with two `bridges.ExternalInitiator` records created via `BridgeORM().CreateExternalInitiator`, each with distinct `AccessKey`/`OutgoingToken` values (mirroring `core/web/external_initiators_controller_test.go` setup style).
2. Create a session client with only the minimal/view role using the test HTTP client helpers used elsewhere in `core/web/external_initiators_controller_test.go`.
3. Issue `GET /v2/external_initiators` with that low-privilege session.
4. Assert HTTP 200 and that the JSON body's `externalInitiators` array includes `accessKey`/`outgoingToken` fields matching the plaintext values of the OTHER (not caller-associated) external initiator records — proving cross-subscriber secret disclosure to a low-privileged caller.
5. As a control, assert the same fields are absent/redacted if the fix (recommendation) is applied, expecting the test to fail against the current implementation and pass after remediation.

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
