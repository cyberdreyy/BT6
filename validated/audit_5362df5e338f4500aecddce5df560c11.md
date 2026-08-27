### Title
Stale Zone-B DON Membership Cache Allows Non-Allowlisted Workflow Owners to Bypass Vault `GetSecrets` Restriction - (File: core/capabilities/vault/zone_b_restriction.go)

### Summary
`zoneBRestrictor.isZoneBWorkflowDON` caches the last successfully-resolved "is this DON in the `zone-b` family" result per `WorkflowDonID` and falls back to that cached boolean whenever a live capabilities-registry lookup transiently fails. If a DON's family membership is authoritatively changed (e.g. a DON is migrated/added into the `zone-b` family) and the very next registry lookup for that DON coincides with a transient registry error, the restrictor returns the stale `false` cached value instead of failing closed, permanently (until a successful lookup happens again) allowing an unprivileged workflow owner in that now-restricted DON to read Vault secrets without ever passing the `ownerAllowed` allowlist gate. This mirrors the FiatDAO `Blocklist`/`VotingEscrow` bug class: a mutable access-control list is trusted via a cached/stale value, and there is no mechanism to force re-validation or invalidate the cache when the authoritative list changes — except here the "trap" is inverted into a security-relevant allowlist bypass rather than a fund lock.

### Finding Description
`enforce()` is the sole authorization gate for the Vault `GetSecrets` capability call path, invoked directly from `Capability.Execute` before any request-specific processing: [1](#0-0) 

Inside `enforce`, whether a caller's DON must satisfy the `ownerAllowed` allowlist depends entirely on `isZoneBWorkflowDON`: [2](#0-1) 

`isZoneBWorkflowDON` resolves membership from the live capabilities registry and unconditionally caches the boolean result (`true` or `false`) keyed by `workflowDonID`. On a subsequent registry error it returns the cached value rather than failing closed: [3](#0-2) 

The code comment explicitly documents this as an intentional design ("Fall back to the last successfully-resolved membership for this DON; only a never-before-resolved DON fails closed") to avoid a DON-wide outage during transient registry unavailability. However, this creates a window where a DON's membership transition from non-zone-b to zone-b (an authoritative access-restriction change, analogous to the FiatDAO `Blocklist` value being updated) is not honored if the registry glitches on the first lookup after the transition — the stale `false` value is served instead, and the `ownerAllowed` gate (the analog of the "unblock" protection in the report) is silently skipped for every caller in that period.

### Impact Explanation
This is a concrete allowlist-bypass / secret-disclosure vulnerability: workflow owners that are not on `PerOwner.VaultZoneBGetSecretsAllowed` can retrieve Vault-managed secrets for a zone-b DON that was meant to be restricted, purely by having a request land during a transient registry hiccup that happens to coincide with the migration window. Because `zone-b` restriction exists specifically to segregate which workflow owners may call `GetSecrets` for a DON, bypassing it constitutes unauthorized access to another party's encrypted secret material via the vault capability, matching the "allowlist bypass" / "key/secret disclosure" categories.

### Likelihood Explanation
Exploitation requires two conditions to coincide: (1) an operator changes a DON's `zone-b` family membership (adding new restriction) and (2) a capabilities-registry lookup for that DON transiently errors (e.g., "not yet synced after startup, or nil mid-update") on or after that change and before a fresh successful resolution occurs. The registry-unavailability condition is explicitly called out in code comments as a real, recurring occurrence ("registry view can be transiently unavailable ... not yet synced after startup, or nil mid-update"), and any workflow owner able to submit a `GetSecrets` request through a DON undergoing this transition can trigger the bypass without any special privilege — they only need to be an unprivileged CRE workflow owner attached to that workflow DON.

### Recommendation
- Do not treat a stale cache entry as authoritative for security-relevant zone transitions; instead, fail closed (deny) when the registry cannot be freshly resolved and the cached result is `false`, while still allowing the transient-availability fallback only when the cached result is `true` (i.e., bias the fallback toward the restrictive outcome, not the permissive one).
- Attach a TTL/staleness bound to `zoneCache` entries so a cached `false` older than a short window cannot be relied upon indefinitely, forcing periodic re-validation against the registry.
- Emit a metric/alert (beyond the existing `Warnw` log) when the fallback path is exercised, since it represents a period of degraded authorization guarantees.

### Proof of Concept
1. Configure DON `X` initially with `Families = ["zone-a"]`; the `VaultZoneBWorkflowGetSecretsRestrictEnabled` gate is enabled.
2. A workflow owner `O` (not on `PerOwner.VaultZoneBGetSecretsAllowed`) calls `Execute`/`GetSecrets` from DON `X` — `isZoneBWorkflowDON` resolves `false` and caches `{X: false}`.
3. Operator migrates DON `X` into the `zone-b` family (`Families = ["zone-b"]`) to restrict it going forward.
4. Immediately after, the capabilities registry experiences a transient sync error (as documented in `isZoneBWorkflowDON`'s comment) on the next `DONByID(X)` call.
5. Owner `O` again calls `GetSecrets` from DON `X`. `DONByID` errors, so `isZoneBWorkflowDON` returns the cached stale value `false` (line 109-113 of `zone_b_restriction.go`), `enforce` treats the caller as non-zone-b, skips the `ownerAllowed.AllowErr` check, and the request proceeds to `handleRequest`, disclosing vault secrets to an owner who should have been denied.

**Uncertainty note:** I was unable to trace how quickly/reliably the capabilities registry actually enters this "not yet synced / nil mid-update" transient-error state in production deployments, nor how frequently DON family membership changes occur relative to registry sync cycles — this affects real-world likelihood but not the validity of the code-level flaw itself.

### Citations

**File:** core/capabilities/vault/capability.go (L107-112)
```go
	// Reject GetSecrets reads from a restricted zone-b workflow DON before the
	// request enters the OCR queue, so non-allowlisted owners get a deterministic
	// rejection and zone-a / gateway paths are unaffected.
	if err := s.zoneBRestrictor.enforce(ctx, request.Metadata.WorkflowDonID); err != nil {
		return capabilities.CapabilityResponse{}, err
	}
```

**File:** core/capabilities/vault/zone_b_restriction.go (L80-94)
```go
	isZoneB, err := z.isZoneBWorkflowDON(ctx, workflowDonID)
	if err != nil {
		// Fail closed: if we cannot authoritatively resolve the caller's zone, do
		// not proceed. The registry is in-process, so this only fires for an
		// unknown/unregistered WorkflowDonID.
		return err
	}
	if !isZoneB {
		return nil
	}

	if err := z.ownerAllowed.AllowErr(ctx); err != nil {
		return fmt.Errorf("zone-b workflow DON may only read vault secrets for allowlisted workflow owners: %w", err)
	}
	return nil
```

**File:** core/capabilities/vault/zone_b_restriction.go (L100-121)
```go
func (z *zoneBRestrictor) isZoneBWorkflowDON(ctx context.Context, workflowDonID uint32) (bool, error) {
	don, err := z.capabilitiesRegistry.DONByID(ctx, workflowDonID)
	if err != nil {
		// The registry view can be transiently unavailable (e.g. not yet synced
		// after startup, or nil mid-update: DONByID returns "metadataRegistry
		// information not available"). That error is not specific to zone-b
		// callers, so failing closed here would block every vault GetSecrets read
		// DON-wide. Fall back to the last successfully-resolved membership for this
		// DON; only a never-before-resolved DON fails closed.
		if cached, ok := z.cachedZoneMembership(workflowDonID); ok {
			z.lggr.Warnw("capabilities registry lookup failed; using cached zone-b membership",
				"workflowDonID", workflowDonID, "isZoneB", cached, "err", err)
			return cached, nil
		}
		return false, fmt.Errorf("could not resolve caller workflow DON %d for zone-b vault read restriction: %w", workflowDonID, err)
	}
	// Case-insensitive match: family casing may vary across registry sources.
	isZoneB := slices.ContainsFunc(don.Families, func(family string) bool {
		return strings.EqualFold(family, zoneBFamily)
	})
	z.storeZoneMembership(workflowDonID, isZoneB)
	return isZoneB, nil
```
