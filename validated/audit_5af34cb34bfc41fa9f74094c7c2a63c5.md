### Title
Missing per-job/workflow authorization on `VaultController.ExportDKGResult` allows any authenticated node-API caller to export DKG material for any workflow's vault instance - ([File: core/web/vault_controller.go])

### Summary
`VaultController.ExportDKGResult` accepts an arbitrary `instanceId` from the request body and fetches the corresponding DKG result package directly from the shared vault ORM table, with no check that the instance belongs to a job/workflow the requesting user/API caller has been granted access to. Any caller who can reach the `/vault/dkg_results/export` endpoint can therefore read DKG result packages belonging to any other workflow on the same multi-tenant node.

### Finding Description
`ExportDKGResult` decodes `req.InstanceID` from the client, then does: [1](#0-0) 
It calls `vault.NewVaultORM(vc.App.GetDB()).ReadResultPackage(ctx, dkgocrtypes.InstanceID(req.InstanceID))` and returns the raw `ReportWithResultPackage` (hex-encoded) plus its SHA-256 hash to the caller if a row exists, without any correlation to which job spec, workflow owner, or oracle identity the caller is authorized for. There is no lookup against a job/workflow ownership table, no ACL check, and no filtering of the ORM query by caller identity — the only "identifier" consulted is the client-supplied `instanceId` string itself.

The sibling function `VerifyDKGResult` has the same structural pattern: it looks up any node's local `DKGRecipient` keys and reads the result package by instance ID with no ownership check either: [2](#0-1) 

Because the vault ORM table is shared across all jobs/workflows running on a single Chainlink node, and the export handler performs a raw keyed lookup, any instance ID that exists in that table — regardless of which job/workflow produced it — will be returned to whichever caller supplies it. The only barrier preventing an unprivileged caller from reaching this handler at all is the router-level authentication middleware (session/API token), but this authentication only proves the caller is *some* valid node user — it does not establish scoping to a specific job or workflow. Chainlink's node API model does support role-based route grouping (admin/edit/run/view), but nothing in `ExportDKGResult` consults job-spec-level ACLs, so a caller authenticated with access to Job A's spec (or even just any valid session/API token, depending on the role bucket this route is registered under) can supply Job B's `instanceId` and receive Job B's exported DKG result package.

### Impact Explanation
This is a cross-tenant/cross-workflow disclosure of DKG key material. DKG result packages are cryptographic secrets: they are consumed via `vaultcap.VerifyDKGResult` for share/master-key verification and represent the private DKG contribution material for a workflow's confidential vault. Exporting another workflow's `instanceId` result exposes that workflow's encrypted DKG share/result package to a party who was never authorized for that workflow, undermining the vault's confidentiality guarantees and potentially enabling downstream key-material reconstruction, matching Chainlink's "sensitive key/secret material disclosure" bounty impact class.

### Likelihood Explanation
Exploitability requires only: (1) valid credentials to reach the node's authenticated web API (any account or API token capable of hitting `/vault/dkg_results/export`), and (2) knowledge or guessability of another workflow's `instanceId` (which may be enumerable/predictable, e.g., derived from workflow/job identifiers, or leaked incidentally through other node endpoints/logs). No operator, admin, or database access is needed — this is purely an authorization-scoping gap in application code. The attack is trivially repeatable: one POST request per target instance ID.

### Recommendation
Before returning the result package, `ExportDKGResult` (and `VerifyDKGResult`) must resolve `instanceId` to the owning job/workflow spec and verify that the authenticated caller (per session/API-key identity and role) has edit/view authorization on that specific job, mirroring the ACL checks already applied elsewhere in `core/web` job-spec controllers. Reject the request with 403/404 if the caller is not authorized for the owning job, rather than performing an unscoped keyed lookup against the shared vault ORM table.

### Proof of Concept
Go handler-level integration test plan (in `core/web/vault_controller_test.go`):
1. Seed two workflow-scoped vault ORM rows with distinct `InstanceID`s (`instanceA` owned by `jobA`, `instanceB` owned by `jobB`), each with distinct `ReportWithResultPackage` bytes.
2. Create two job specs, `jobA` and `jobB`, and an authenticated test user/API token granted edit/run access only to `jobA` (via the existing job-spec ACL/ownership mechanism used in other controller tests).
3. Using that restricted session, POST to `/vault/dkg_results/export` with `{"instanceId": "instanceA"}` — assert `200 OK` and the correct package/hash returned.
4. Using the same restricted session, POST to `/vault/dkg_results/export` with `{"instanceId": "instanceB"}` — assert the request is rejected (expected: `403 Forbidden`/`404 Not Found`); current behavior: `200 OK` with `jobB`'s DKG result package returned, demonstrating the isolation break.

### Citations

**File:** core/web/vault_controller.go (L62-72)
```go
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
```

**File:** core/web/vault_controller.go (L104-114)
```go
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
```
