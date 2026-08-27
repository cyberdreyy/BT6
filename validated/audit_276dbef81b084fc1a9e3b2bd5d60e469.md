### Title
GET /v2/external_initiators leaks external initiator `AccessKey` credentials to any authenticated caller - ([File: core/web/presenters/external_initiators.go])

### Finding Description
`ExternalInitiatorResource` (used by `ExternalInitiatorsController.Index`) includes the raw `AccessKey` field in its JSON serialization: [1](#0-0) 
and `NewExternalInitiatorResource` copies `ei.AccessKey` directly from the DB-backed `bridges.ExternalInitiator` model into the presenter with no redaction: [2](#0-1) 

`Index` builds this resource for every external initiator returned from the ORM and serializes the full list to the client: [3](#0-2) 

The `AccessKey`+`Secret` pair is the credential pair verified by `AuthenticateExternalInitiator` in `core/web/auth/auth.go`, which reads `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers, looks up the initiator by `AccessKey`, and authenticates using the `Secret`: [4](#0-3) 

Because the presenter only omits `Secret`/`HashedSecret`/`Salt` (which are not stored on `bridges.ExternalInitiator` in plaintext anyway) but returns `AccessKey` verbatim, any caller who can reach `GET /v2/external_initiators` learns every configured external initiator's valid `AccessKey` in plaintext. This converts the two-factor `AccessKey`+`Secret` bootstrap-authentication scheme into a single-factor scheme from the attacker's perspective — the attacker no longer needs to guess/brute-force the `AccessKey`, only the `Secret`, cutting the search space of the credential pair by one dimension and allowing the attacker to target `AuthenticateExternalInitiator` requests at exactly the initiators that exist.

I was unable to confirm within this session, from the parts of `core/web/router.go` I could inspect, whether the `Index` route is wrapped with `auth.RequiresEditRole`/`RequiresAdminRole` or is reachable by any authenticated session/API-token user regardless of role (`RequiresRunRole` only blocks `UserRoleView`, `RequiresEditRole` also blocks `UserRoleRun`). This is a gap in my verification — the severity of this finding depends on that role gate, which should be checked directly in `core/web/router.go`.

### Impact Explanation
Disclosure of `AccessKey` material for all configured external initiators is a credential-exposure issue: it materially reduces the attacker's effort to bootstrap a forged external-initiator request against `AuthenticateExternalInitiator`, since the `AccessKey` is normally treated as sensitive (analogous to a username/API-key half of a credential pair) that should not be handed out to arbitrary authenticated users, especially low-privilege ones. This maps to Chainlink's "sensitive information disclosure" / credential-exposure impact class rather than direct RCE or fund loss, because full exploitation still requires guessing/obtaining the corresponding `Secret`.

### Likelihood Explanation
Exploitability requires only that the attacker holds a valid, low-privilege authenticated session or API token capable of reaching `GET /v2/external_initiators` — no external-initiator credentials or elevated role are needed. Whether this is truly reachable by a `view`-role user depends on the (unverified in this session) role wrapper on the `Index` route in `core/web/router.go`. If the route requires only session/API-token authentication without an edit/admin role gate, the likelihood is high and trivially repeatable (single unauthenticated-of-privilege GET request). If the route is gated behind `RequiresEditRole`/`RequiresAdminRole`, exposure is limited to already highly-privileged users, reducing but not eliminating impact (still exposes credential material beyond the minimum necessary to those users, and to any future consumer of the same JSON, e.g. logs, browser extensions, or a compromised admin session).

### Recommendation
Do not include `AccessKey` in `ExternalInitiatorResource`/`Index` responses at all, or mask it (e.g., return only a prefix, or omit entirely) since operators who need to see it can re-derive/rotate it via `Create`. If it must be exposed for operational reasons, restrict `Index` to `RequiresAdminRole` explicitly and confirm this in `core/web/router.go`.

### Proof of Concept
1. Unit test in `core/web/presenters/external_initiators_test.go`: construct a `bridges.ExternalInitiator{AccessKey: "test-access-key", ...}`, call `NewExternalInitiatorResource`, `json.Marshal` the result, and assert the marshaled JSON contains `"accessKey":"test-access-key"` — demonstrating the field is present and not redacted.
2. Handler-level integration test in `core/web/external_initiators_controller_test.go`: create an external initiator via `Create`, then authenticate as a low-privilege user (e.g., `UserRoleRun` or `UserRoleView` if permitted by the router) and call `GET /v2/external_initiators`; assert the response body includes the initiator's real `AccessKey`, and separately verify via `router.go` inspection what minimum role is required to reach this route.

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

**File:** core/web/presenters/external_initiators.go (L67-77)
```go
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

**File:** core/web/auth/auth.go (L119-141)
```go
func AuthenticateExternalInitiator(c *gin.Context, store Authenticator) error {
	ctx := c.Request.Context()
	eia := &auth.Token{
		AccessKey: c.GetHeader(static.ExternalInitiatorAccessKeyHeader),
		Secret:    c.GetHeader(static.ExternalInitiatorSecretHeader),
	}

	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
	}

	ok, err := bridges.AuthenticateExternalInitiator(eia, ei)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}
```
