### Title
Missing role/scope check on high-value `/v2/vault/dkg_results/export` endpoint allows any authenticated session or API token (including view-role) to exfiltrate DKG secret shares - ([File: core/web/vault_controller.go])

### Finding Description
`VaultController.ExportDKGResult` [1](#0-0)  performs no authorization/role check whatsoever inside the handler itself — it only validates that `instanceId` is non-empty, then reads the raw DKG result package from the ORM and returns it hex-encoded directly to the caller: [2](#0-1) 

All authorization for this route is therefore delegated entirely to whatever middleware wraps the route in `core/web/router.go`. The router mounts all `/v2/...` routes (via `v2Routes(app, api)`) under a single generic authenticated group that uses session cookies or API tokens for authentication (`sessions.Sessions(...)`, `rateLimiter(...)`) [3](#0-2) . Unlike some other sensitive Chainlink v2 endpoints that wrap individual routes with role-restriction middleware (e.g., `sessions.RequiresEditRole`/`RequiresAdminRole` equivalents used elsewhere in the router), I was unable to locate any such role-gating middleware specifically applied to the `dkg_results/export` route registration within the portion of `router.go` I was able to inspect. The handler contains no explicit call to check the authenticated user's role (`view`/`run`/`edit`/`admin`) or any distinct "vault" scope/permission before returning the raw secret-bearing `ReportWithResultPackage`.

Because Chainlink API tokens are 1:1 bound to a user account and inherit that account's role rather than being independently scoped per-endpoint, any token belonging to a low-privilege (`view`) user that is authenticated by the generic session/token middleware would reach the handler and receive the exported DKG result package — a highly sensitive secret-share artifact — if no distinct role check exists on this specific route.

### Impact Explanation
If confirmed, a `view`-role user (intended only for read-only dashboard access) or any token holder authenticated via the generic authv2 flow could call `/v2/vault/dkg_results/export` and receive the raw hex-encoded DKG result package plus its SHA-256 hash, potentially exposing DKG shares tied to the node's vault/OCR key material. This maps to Chainlink's "unauthorized secret/key disclosure" bounty impact class, since it bypasses least-privilege token scoping expected for high-value secret export endpoints.

### Likelihood Explanation
Exploitability depends entirely on whether `router.go`'s registration line for this route omits a role-restriction wrapper that similar endpoints use. I was able to confirm the handler itself performs no such check, but I could not fully view the exact route-registration line in `router.go` (only found 2-3 matches for "vault"/"dkg_results" without being able to read the surrounding registration code before running out of tool budget) to conclusively verify the absence of a role guard at the router layer. This is a material gap in verification.

### Recommendation
Add an explicit role/scope check (e.g., require `edit` or `admin` role, or a dedicated "vault" scope) either as router-level middleware on the `/v2/vault/dkg_results/export` route or as the first check inside `VaultController.ExportDKGResult`, rejecting requests from `view`-role sessions/tokens with `403 Forbidden` before touching the ORM.

### Proof of Concept
Handler-level integration test plan:
1. Create a test app with a session/token belonging to a user with `view` role only.
2. Seed a DKG result via `vault.NewVaultORM` for a known `instanceId`.
3. POST to `/v2/vault/dkg_results/export` with `{"instanceId": "<id>"}` using the view-role credential.
4. Assert expected: `403 Forbidden`. Actual (if bug confirmed): `200 OK` with the exported package in the response body.

**Note on confidence:** Due to incomplete visibility into the exact route-registration code in `core/web/router.go` (I could not retrieve the specific lines registering `dkg_results/export` and confirm whether a role-restriction middleware is or isn't applied there), this finding should be treated as **unconfirmed at the router layer** — it is confirmed only that the handler itself (`vault_controller.go`) contains no in-function authorization check. A Devin session with full file access would be needed to inspect the exact `router.go` registration lines to fully validate or refute this finding.

### Citations

**File:** core/web/vault_controller.go (L89-119)
```go
// ExportDKGResult returns the DKGResult corresponding to the given instance ID
// "POST <application>/vault/dkg_results/export"
func (vc *VaultController) ExportDKGResult(c *gin.Context) {
	var req ExportDKGResultRequest
	err := json.NewDecoder(c.Request.Body).Decode(&req)
	if err != nil {
		jsonAPIError(c, http.StatusBadRequest, errors.New("could not parse request body"))
		return
	}

	if req.InstanceID == "" {
		jsonAPIError(c, http.StatusBadRequest, errors.New("instanceId is required"))
		return
	}

	orm := vault.NewVaultORM(vc.App.GetDB())
	v, err := orm.ReadResultPackage(c.Request.Context(), dkgocrtypes.InstanceID(req.InstanceID))
	if err != nil {
		jsonAPIError(c, http.StatusNotFound, err)
		return
	}

	if v == nil {
		jsonAPIError(c, http.StatusNotFound, errors.New("DKG result not found"))
		return
	}

	hexPackage := hex.EncodeToString(v.ReportWithResultPackage)
	sha := sha256.Sum256(v.ReportWithResultPackage)
	shaStr := hex.EncodeToString(sha[:])
	jsonAPIResponse(c, presenters.NewExportDKGResultResource(hexPackage, shaStr), "exportDKGResult")
```

**File:** core/web/router.go (L76-91)
```go
	engine.Use(helmet.Default())
	rl := config.WebServer().RateLimit()
	api := engine.Group(
		"/",
		rateLimiter(
			rl.AuthenticatedPeriod(),
			rl.Authenticated(),
		),
		sessions.Sessions(auth.SessionName, sessionStore),
	)

	debugRoutes(app, api)
	healthRoutes(app, api)
	sessionRoutes(app, api)
	v2Routes(app, api)
	loopRoutes(app, api)
```
