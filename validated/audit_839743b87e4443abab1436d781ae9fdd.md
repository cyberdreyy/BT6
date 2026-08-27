### Title
View-role user can list ExternalInitiator AccessKeys via unprotected GET /v2/external_initiators - ([File: core/web/external_initiators_controller.go])

### Summary
The `ExternalInitiatorsController.Index` endpoint is registered in `core/web/router.go` without a `RequiresEditRole` (or higher) wrapper, unlike `Create`/`Destroy`. The handler returns `presenters.ExternalInitiatorResource`, which includes the plaintext `AccessKey` field for every External Initiator record, so any authenticated user with only View role can enumerate all `AccessKey`/`OutgoingToken` values.

### Finding Description
`ExternalInitiatorsController.Index` (core/web/external_initiators_controller.go:50-59) calls `eic.App.BridgeORM().ExternalInitiators(ctx, offset, size)` and maps each row through `presenters.NewExternalInitiatorResource`. That presenter (core/web/presenters/external_initiators.go:57-77) explicitly serializes: [1](#0-0) 
including `AccessKey` and `OutgoingToken` in plaintext JSON fields (`accessKey`, `outgoingToken`), with no redaction.

By contrast, `Create` (core/web/external_initiators_controller.go:62-100) and `Destroy` (core/web/external_initiators_controller.go:103-118) are mutating actions requiring the Edit role per the router's stated wrapper convention (`RequiresEditRole`), while `Index` is a read-only listing route reachable by any authenticated session/API token regardless of role, since it is not wrapped with the same role-gating middleware. This breaks role-exactness: a View-role credential — the minimum privilege granted to read-only dashboard users — can pull every `ExternalInitiator`'s `AccessKey`, which is the credential used by `AuthenticateExternalInitiator` middleware to authorize webhook calls into the node (`bridges.ExternalInitiator`, core/bridges/external_initiator.go). This is a real secret leaving its intended trust boundary: a View-only actor obtains External Initiator access keys they should never see, and can subsequently attempt to replay/use these leaked `AccessKey` values against `AuthenticateExternalInitiator`-protected endpoints, impersonating the corresponding external initiator.

### Impact Explanation
This is a secrets-disclosure / broken access-control finding: External Initiator `AccessKey` (and `OutgoingToken`) values, which authenticate external systems submitting job runs to the node, are disclosed to under-privileged (View-role) users. An attacker with only View role gains material that lets them authenticate as a legitimate External Initiator against `AuthenticateExternalInitiator`-guarded endpoints (e.g., triggering webhook job runs), which corresponds to Chainlink's "unauthorized job run" / "credential or key disclosure" bounty impact classes.

### Likelihood Explanation
Preconditions are minimal: any authenticated node session or API token with View role (the lowest privilege level that still grants API access) can call `GET /v2/external_initiators`. No admin/edit privilege or misconfiguration is required, and the leak is deterministic and repeatable on every call as long as ExternalInitiator records exist.

### Recommendation
1. Wrap the `Index` route for `/v2/external_initiators` with the same `RequiresEditRole` (or a new `RequiresViewRole`-but-redacted) middleware applied to `Create`/`Destroy`, or otherwise restrict it to Admin/Edit roles.
2. Regardless of role, redact `AccessKey`/`OutgoingToken`/any secret material from `ExternalInitiatorResource` returned by `Index`; only return non-secret identifying fields (`name`, `url`, `createdAt`, `updatedAt`), consistent with how `Create`/`NewExternalInitiatorAuthentication` intentionally scopes secret exposure to the one-time creation response.

### Proof of Concept
Go handler-level integration test (extending `core/web/external_initiators_controller_test.go`):
1. Seed the app with an `ExternalInitiator` via `BridgeORM().CreateExternalInitiator` containing a known `AccessKey`/`OutgoingToken`.
2. Construct an HTTP client authenticated with a **View-role** session/API token (as used elsewhere in `core/web` tests for role-based access checks).
3. Call `GET /v2/external_initiators` with that client.
4. Assert the response status is `200 OK` (not `401/403`), proving the missing role wrapper.
5. Assert the JSON body's `data[].attributes.accessKey` and `outgoingToken` fields are populated with the seeded plaintext values, proving secret disclosure to a View-role principal.
6. (Optional extended PoC) Use the leaked `accessKey`/`outgoingToken` to call a webhook/job-run endpoint guarded by `AuthenticateExternalInitiator` and show successful authentication, demonstrating downstream impersonation.

### Citations

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
