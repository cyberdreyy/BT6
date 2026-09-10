### Title
Replayable preregistration signatures let anyone resurrect an expired withdrawal-credentials binding, blocking a validator owner's legitimate deposit and destroying its Gwei - (File: `specs/_features/eip8205/beacon-chain.md`)

### Summary
`process_preregistration_request` accepts any `PreregistrationRequest` whose BLS signature verifies over `(pubkey, withdrawal_credentials)` under a domain that is fork-agnostic and contains no nonce, sequence number, or freshness marker [1](#0-0) . Once a stored preregistration expires and is no longer "active," the *exact same* historical signed request — publicly visible on-chain from when it was first included — can be resubmitted by anyone to reinstate the stale binding, because the only guard against re-registration is "is there currently an *active* binding for this pubkey," not "has this signature ever been consumed" [2](#0-1) . This lets an attacker overwrite/reinstate a withdrawal-credentials binding the pubkey owner no longer wants, which in turn causes a subsequent, correctly-signed deposit for that pubkey to be silently discarded by `process_deposit_request` because its withdrawal credentials no longer match the (attacker-reinstated) stored binding [3](#0-2) .

### Finding Description
The `approve`/`transferFrom` bug class breaks the equality "the latest intended authorization is the one in effect" because `approve` unconditionally overwrites the allowance without regard to whether the previous allowance was already partially consumed, letting a racer combine old and new allowances. The EIP-8205 preregistration mechanism has the analogous flaw at the authorization layer: the stored binding for a pubkey can always be overwritten/reinstated by *replaying an old, still cryptographically valid signature*, because:

- `PreregistrationRequest` = `(pubkey, withdrawal_credentials, signature)` with no nonce or expiry embedded in the signed payload [4](#0-3) .
- The signing domain is deliberately fork-agnostic and tied only to `genesis_validators_root`, explicitly documented as remaining valid forever, "like a deposit signature" [5](#0-4) .
- Unlike a deposit (which permanently consumes its signature by creating a validator, after which `pubkey in [v.pubkey ...]` blocks any replay), a preregistration is designed to *expire and be replaced*, and `is_valid_preregistration_signature` re-verifies the exact same signature every time with no historical "already used" bit [6](#0-5) .
- `process_preregistration_request` only checks whether an *active* binding currently exists; if not, it happily stores (or replaces in place) a new `StoredPreregistration` from the replayed request, complete with a freshly computed `expiry_slot` [7](#0-6) .

Concrete state trace:
1. Validator owner Alice signs and submits a preregistration binding `pubkey P → WC=X` (e.g., an old custodian address she later rotates away from). It is stored and eventually expires per `is_active_preregistration` (`parent_slot >= expiry_slot`) [8](#0-7) .
2. Alice now wants to bind `P → WC=Y` (her real, current custodian) and broadcasts a correctly signed `PreregistrationRequest(P, Y, sig_Y)`, followed later by a real deposit `DepositRequest(P, Y, amount, sig_deposit)`.
3. Attacker Bob, having observed Alice's original `PreregistrationRequest(P, X, sig_X)` on-chain history, resubmits it. Because no active binding currently exists for `P`, all guard checks pass and `sig_X` reverifies exactly as before, so Bob's replay is accepted and stores `StoredPreregistration(P, X, new_expiry)` [9](#0-8) .
4. If Bob's replay lands before/instead of Alice's `Y` request in the same or an earlier slot, Alice's own `(P, Y, sig_Y)` request now hits the "already active" no-op branch and is dropped [10](#0-9) .
5. Alice's subsequent legitimate deposit `(P, Y, amount, sig_deposit)` is then compared against the reinstated active binding `WC=X`, fails the equality check, and is discarded by `process_deposit_request` without being appended to `pending_deposits` — i.e., without ever crediting `Y`'s balance [11](#0-10) .

The ETH backing that deposit was already locked into the EL deposit contract before the CL-side discard; there is no refund path defined by the spec for a discarded deposit request. That Gwei is therefore destroyed relative to Alice's intent, entirely at Bob's discretion, using only publicly observable chain data and no privileged access.

### Impact Explanation
This is a Critical-class equality break per the given rubric: Gwei is destroyed (a deposit that should have credited validator `P` with `amount` under `WC=Y` is discarded, with no compensating balance change), and it is achieved by an unprivileged third party who mutates a validator-pubkey's binding without that validator's current authority — purely through public replay of previously-broadcast, still-signature-valid data. No malicious node, client bug, partition, or coalition is needed.

### Likelihood Explanation
The attack requires only:
- Observing a previously included `PreregistrationRequest` for the target pubkey (public chain data, guaranteed to exist for every preregistering validator's first — or any expired — binding).
- Submitting an ordinary execution-layer transaction that generates a `PreregistrationRequest` with the exact same fields, timed for any window in which no active binding currently exists for that pubkey (which happens deterministically whenever a binding expires, and is also racy immediately after any of the victim's own re-registration attempts).

Because the exploit needs no validator key, no majority stake, and no coordination, and the "attack window" recurs every time a binding needs to be refreshed or replaced, likelihood is high wherever the preregistration feature is used by delegated-staking operators who rotate withdrawal credentials.

### Recommendation
Bind preregistration signatures to a single-use context so a signature cannot be replayed after its record has been superseded or expired. Options:
- Include a monotonically increasing nonce or the current `state.slot`/`expiry_slot` inside the signed `ValidatorPreregistration` payload, and store the last-used nonce per pubkey (or simply record that a pubkey has ever had a preregistration removed/expired) so an identical old signature can never be re-accepted.
- Alternatively, require that a *replacement* preregistration for a pubkey with a prior (even expired) record must strictly increase a signed sequence counter, mirroring how deposits are made single-use via permanent validator creation.

### Proof of Concept
```
1. Alice: submit PreregistrationRequest(pubkey=P, wc=X, sig=sig_X)
   -> state.validator_preregistrations = [StoredPreregistration(P, X, expiry=E1)]
2. Wait until slot >= E1 (binding no longer active; may or may not be swept yet)
3. Alice: submit PreregistrationRequest(pubkey=P, wc=Y, sig=sig_Y)   # her real, current WC
   Bob (attacker): replay PreregistrationRequest(pubkey=P, wc=X, sig=sig_X)  # old, public, still-valid signature
   -> If Bob's replay is processed while Alice's is pending/same block and ordered first:
        process_preregistration_request(state, replay_X)  -> stores StoredPreregistration(P, X, expiry=E2), active
        process_preregistration_request(state, Y_request) -> get_active_preregistration(P) is not None -> no-op, Alice's Y binding is dropped
4. Alice: submit DepositRequest(pubkey=P, wc=Y, amount=A, sig=sig_deposit)  # correct, fully valid deposit
   process_deposit_request(state, deposit):
       preregistration = get_active_preregistration(state, P)  # returns StoredPreregistration(P, X, E2)
       deposit.withdrawal_credentials (Y) != preregistration.withdrawal_credentials (X)
       -> return   # deposit silently discarded, pending_deposits unchanged, amount A never credited
```
Result: Alice's `amount A` of Gwei, already committed on the execution layer, is never reflected on the beacon chain — destroyed relative to her intent — solely because Bob replayed a stale, publicly-known signed message that the spec places no restriction on reusing. [12](#0-11)

### Citations

**File:** specs/_features/eip8205/beacon-chain.md (L86-96)
```markdown
### Domains

*Note*: Preregistration signatures use a fork-agnostic domain computed with
`compute_domain`, so a preregistration signed once remains valid across fork
boundaries, like a deposit signature. Unlike deposit signatures, the domain is
bound to the chain through `genesis_validators_root`, which prevents
cross-network replay.

| Name                     | Value                      |
| ------------------------ | -------------------------- |
| `DOMAIN_PREREGISTRATION` | `DomainType('0x11000000')` |
```

**File:** specs/_features/eip8205/beacon-chain.md (L203-208)
```markdown
```python
class PreregistrationRequest(Container):
    pubkey: BLSPubkey
    withdrawal_credentials: Bytes32
    signature: BLSSignature
```
```

**File:** specs/_features/eip8205/beacon-chain.md (L229-241)
```markdown
```python
def is_valid_preregistration_signature(state: BeaconState, request: PreregistrationRequest) -> bool:
    preregistration = ValidatorPreregistration(
        pubkey=request.pubkey,
        withdrawal_credentials=request.withdrawal_credentials,
    )
    domain = compute_domain(
        DOMAIN_PREREGISTRATION,
        genesis_validators_root=state.genesis_validators_root,
    )
    signing_root = compute_signing_root(preregistration, domain)
    return bls.Verify(request.pubkey, signing_root, request.signature)
```
```

**File:** specs/_features/eip8205/beacon-chain.md (L251-255)
```markdown
```python
def is_active_preregistration(state: BeaconState, preregistration: StoredPreregistration) -> bool:
    parent_slot = state.latest_execution_payload_bid.slot
    return parent_slot < preregistration.expiry_slot
```
```

**File:** specs/_features/eip8205/beacon-chain.md (L383-461)
```markdown
##### New `process_preregistration_request`

```python
def process_preregistration_request(state: BeaconState, request: PreregistrationRequest) -> None:
    pubkey = request.pubkey

    # An active binding for this pubkey makes the new request a no-op,
    # whether the duplicate is exact or conflicting
    if get_active_preregistration(state, pubkey) is not None:
        return

    # A pubkey with an existing validator cannot be preregistered
    if pubkey in [validator.pubkey for validator in state.validators]:
        return

    # A pubkey with a valid pending deposit cannot be preregistered
    if is_pending_validator(state.pending_deposits, pubkey):
        return

    # The capacity check counts only active records, so the timing of the
    # garbage-collection sweep does not affect admission. It applies to
    # appends and to replacements alike, since both create an active binding
    active_preregistrations = [
        preregistration
        for preregistration in state.validator_preregistrations
        if is_active_preregistration(state, preregistration)
    ]
    if len(active_preregistrations) >= PREREGISTRATIONS_LIMIT:
        return

    if not is_valid_preregistration_signature(state, request):
        return

    preregistration = StoredPreregistration(
        pubkey=pubkey,
        withdrawal_credentials=request.withdrawal_credentials,
        expiry_slot=state.slot + PREREGISTRATION_EXPIRY_SLOTS,
    )
    index = get_stored_preregistration_index(state, pubkey)
    if index is not None:
        # Replace the expired record in place
        state.validator_preregistrations[index] = preregistration
    else:
        state.validator_preregistrations.append(preregistration)
```

##### Modified `process_deposit_request`

*Note*: The function `process_deposit_request` is modified to enforce an active
preregistration binding.

```python
def process_deposit_request(state: BeaconState, deposit_request: DepositRequest) -> None:
    # [New in EIP8205]
    preregistration = get_active_preregistration(state, deposit_request.pubkey)
    if preregistration is not None:
        if deposit_request.withdrawal_credentials != preregistration.withdrawal_credentials:
            return

        if not is_valid_deposit_signature(
            deposit_request.pubkey,
            deposit_request.withdrawal_credentials,
            deposit_request.amount,
            deposit_request.signature,
        ):
            return

        remove_stored_preregistration(state, deposit_request.pubkey)

    state.pending_deposits.append(
        PendingDeposit(
            pubkey=deposit_request.pubkey,
            withdrawal_credentials=deposit_request.withdrawal_credentials,
            amount=deposit_request.amount,
            signature=deposit_request.signature,
            slot=state.slot,
        )
    )
```
```
