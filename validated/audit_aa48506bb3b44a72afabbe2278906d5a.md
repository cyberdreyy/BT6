## Title
Front‑run preregistration binding causes a legitimate deposit's ETH to be permanently destroyed - (`specs/_features/eip8205/beacon-chain.md`)

### Summary
EIP‑8205 introduces a pubkey → withdrawal‑credentials "preregistration" binding intended to stop deposit front‑running for delegated/pooled staking deployments [1](#0-0) . Because the very same key holder who is supposed to cooperate with the depositing protocol can instead submit a *self‑signed* preregistration for a different withdrawal address before the protocol's real deposit lands, the legitimate deposit is not merely mis-credited — it is silently dropped and the deposited Gwei is destroyed with no validator ever credited.

### Finding Description
`process_preregistration_request` accepts a preregistration for `request.pubkey` as long as it carries a valid BLS signature by that pubkey and no other active binding already exists: [2](#0-1) 

Critically, a duplicate or *conflicting* subsequent preregistration for the same pubkey is a no‑op while a binding is active:

```
# An active binding for this pubkey makes the new request a no-op,
# whether the duplicate is exact or conflicting
if get_active_preregistration(state, pubkey) is not None:
    return
```

`process_deposit_request` then enforces the *first* stored binding against any incoming deposit for that pubkey, and if the withdrawal credentials don't match, the deposit is simply discarded (never appended to `pending_deposits`): [3](#0-2) 

```
preregistration = get_active_preregistration(state, deposit_request.pubkey)
if preregistration is not None:
    if deposit_request.withdrawal_credentials != preregistration.withdrawal_credentials:
        return
    ...
state.pending_deposits.append(...)
```

The threat model EIP‑8205 targets is a delegated staking deployment: an operator generates a validator keypair and is expected to sign a deposit (or preregistration) committing that pubkey to the pool's withdrawal address. Nothing stops that same operator from instead broadcasting their *own* preregistration request for the pubkey with an attacker‑controlled `withdrawal_credentials` before the pool's transaction (deposit or preregistration) is included — this is exactly the front‑running/reorg race described in the external report, just moved one step earlier in the flow. Once the attacker's preregistration is included:

1. The pool's subsequent legitimate preregistration for the same pubkey is a silent no‑op (`get_active_preregistration(...) is not None`), so the pool cannot even detect/correct the binding on‑chain.
2. Any deposit the pool later submits for that pubkey — carrying real ETH already consumed by the EL deposit mechanism — is compared against the attacker's binding, fails the credentials check, and is dropped via a bare `return` in `process_deposit_request`. The ETH is never added to `pending_deposits` and is never credited to any validator or address.
3. This condition persists for the full `PREREGISTRATION_EXPIRY_SLOTS` window (`2**18` slots, ≈36 days) via `is_active_preregistration`, since expiry is judged only against `state.latest_execution_payload_bid.slot` [4](#0-3) [5](#0-4) .

Unlike the pre‑existing "invalid deposit signature is silently dropped" behavior (which is the depositor's own fault), here the depositor's deposit is entirely well‑formed; it is destroyed purely because of an unrelated third‑party transaction that the protocol has no way to preempt or reverse, breaking the equality that a submitted, well-formed deposit's Gwei must end up credited to some validator/balance.

### Impact Explanation
This is a Critical-severity "Gwei destroyed" outcome: real ETH sent to the deposit mechanism on the execution layer is consumed but never credited to a validator balance, and the legitimate depositor has no on-chain remedy while the malicious binding remains active (up to ~36 days). This is strictly worse than the pre‑EIP‑8205 baseline front‑running attack, where a front‑run deposit at least resulted in funds landing on an attacker-controlled validator (theft) rather than being destroyed outright.

### Likelihood Explanation
The only capability required is possession of the pubkey's private key — precisely the party (operator) that delegated/pooled staking protocols must trust to eventually produce a valid deposit or preregistration signature in the first place. No coalition, no majority stake, and no client bug is needed; a single rogue or compromised operator key holder can race the protocol's transaction in the public mempool (or exploit a reorg) to land their own preregistration first.

### Recommendation
Do not let `process_deposit_request` unconditionally drop a mismatched deposit against a preregistration binding; instead consider: (a) allowing a deposit's own signature to take precedence and overwrite/expire a conflicting binding after also verifying the depositor's proof-of-possession, or (b) refunding/queuing mismatched deposits for reprocessing rather than discarding them outright, or (c) requiring the pool's deposit to also be able to invalidate an unauthorized third‑party binding placed on its pubkey (e.g., by proving continuity via the same signature domain/message used for the deposit itself) before expiry.

### Proof of Concept
1. Node operator generates keypair for `pubkey`. Pool agrees on `withdrawal_credentials = POOL_ADDR` and asks operator to sign a preregistration for it (not yet submitted).
2. Operator instead builds and signs their own `PreregistrationRequest(pubkey, withdrawal_credentials=ATTACKER_ADDR, signature=...)` and submits it directly to the EL, getting it included first. `process_preregistration_request` stores `StoredPreregistration(pubkey, ATTACKER_ADDR, expiry_slot=state.slot+PREREGISTRATION_EXPIRY_SLOTS)` [6](#0-5) .
3. Pool's later preregistration request for `POOL_ADDR` is silently dropped as a no-op (`get_active_preregistration(...) is not None`) [7](#0-6) .
4. Pool submits a real deposit transaction (real ETH, valid signature, `withdrawal_credentials=POOL_ADDR`). `process_deposit_request` finds the active `ATTACKER_ADDR` binding, sees the mismatch, and returns without appending to `pending_deposits` [8](#0-7) .
5. Result: pool's deposited Gwei is never credited to any validator or balance — permanently destroyed — for up to `PREREGISTRATION_EXPIRY_SLOTS` slots, purely due to an unprivileged party's earlier transaction.

### Citations

**File:** specs/_features/eip8205/beacon-chain.md (L49-59)
```markdown
This upgrade adds withdrawal credentials preregistration to the beacon chain as
part of the EIP-8205 upgrade. A preregistration binds a validator pubkey to
withdrawal credentials before the validator's first deposit is processed,
protecting delegated staking deployments against deposit front-running.

This document specifies the beacon chain changes required to support these
preregistrations. The upgrade introduces a new request type within the execution
payload, triggered by execution layer transactions, which stores a
pubkey-to-withdrawal-credentials binding in the beacon state. While a binding is
active, a deposit request for the bound pubkey is discarded unless its
withdrawal credentials match the binding and its signature is valid.
```

**File:** specs/_features/eip8205/beacon-chain.md (L106-112)
```markdown
### Execution

| Name                                       | Value                       |
| ------------------------------------------ | --------------------------- |
| `MAX_PREREGISTRATION_REQUESTS_PER_PAYLOAD` | `Uint64(2**2)` (= 4)        |
| `PREREGISTRATIONS_LIMIT`                   | `Uint64(2**19)` (= 524,288) |
| `PREREGISTRATION_EXPIRY_SLOTS`             | `Slot(2**18)` (= 262,144)   |
```

**File:** specs/_features/eip8205/beacon-chain.md (L251-255)
```markdown
```python
def is_active_preregistration(state: BeaconState, preregistration: StoredPreregistration) -> bool:
    parent_slot = state.latest_execution_payload_bid.slot
    return parent_slot < preregistration.expiry_slot
```
```

**File:** specs/_features/eip8205/beacon-chain.md (L386-427)
```markdown
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
```

**File:** specs/_features/eip8205/beacon-chain.md (L435-461)
```markdown
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
