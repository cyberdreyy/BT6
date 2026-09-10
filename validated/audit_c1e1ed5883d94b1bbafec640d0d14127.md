### Title
Preregistration binding change can permanently destroy a depositor's Gwei in EIP‑8205 - (`File: specs/_features/eip8205/beacon-chain.md`)

### Summary
EIP‑8205 lets whoever holds a validator pubkey's private key bind that pubkey to a specific `withdrawal_credentials` value via `process_preregistration_request`. Once a binding is active, `process_deposit_request` silently discards any `DepositRequest` for that pubkey whose `withdrawal_credentials` do not match the binding [1](#0-0) . Because the EL deposit contract irreversibly locks the deposited ETH once the transaction is sent, a deposit that is discarded on the consensus layer results in permanently destroyed Gwei — no validator is credited and no pending deposit is queued. This mirrors the `InsuranceFund#syncDeps()` pattern: a shared, mutable binding that governs where/whether funds land can be changed by an authorized party in the window between a user's fund-moving transaction and its on-chain processing, destroying the user's funds.

### Finding Description
`process_preregistration_request` allows the pubkey owner to (re)bind `withdrawal_credentials` for their own pubkey at any time the binding is inactive, subject only to a capacity limit and signature check [2](#0-1) . `is_active_preregistration` only checks it against the *outstanding parent bid's slot*, so the binding takes effect immediately for any deposit request processed afterward [3](#0-2) .

`process_deposit_request` is modified so that once a binding is active for a pubkey, a `DepositRequest` for that pubkey with different `withdrawal_credentials` is discarded — `return` with no state mutation, no `PendingDeposit` created: [4](#0-3) 

Crucially, `is_pending_validator` in `process_preregistration_request` only guards against a pubkey that already has a `PendingDeposit` in `state.pending_deposits` — it does **not** protect a deposit that has already been submitted on the execution layer but has not yet been included/processed as a `DepositRequest` on the consensus layer [5](#0-4) . Given `ETH1_FOLLOW_DISTANCE`/inclusion latency on the EL→CL request pipeline, there is a real window in which:

1. A depositor (e.g. a delegated-staking pool user) sends an EL deposit transaction for pubkey `P` with `withdrawal_credentials = C1` (the currently expected value, no binding active yet).
2. Before that deposit request is processed by `process_deposit_request`, the holder of `P`'s private key (the pool operator, fully authorized over their own pubkey) submits a `PreregistrationRequest` binding `P` to `withdrawal_credentials = C2 != C1` — a completely valid, signature-checked, in-scope action.
3. When the depositor's `DepositRequest` is later processed, `deposit_request.withdrawal_credentials (C1) != preregistration.withdrawal_credentials (C2)`, so it is discarded per line 439-440 above.
4. The depositor's ETH, already irreversibly sent to the EL deposit contract, is never credited on the beacon chain — it is permanently destroyed.

This is structurally identical to the `InsuranceFund#syncDeps()` bug class: a governance-style, authorized mutation of a shared binding (`vusd` address / `withdrawal_credentials` binding) races an ordinary user transaction (deposit/withdraw) that was formed under the old binding, and the loser's funds are destroyed rather than safely refunded or re-routed.

### Impact Explanation
This breaks the "Gwei destroyed" invariant: ETH that a user locked into the canonical EL deposit contract disappears with no path to recovery, no validator activation, and no compensating pending deposit — solely because of the interleaving of two independently-valid, spec-following transactions. No malicious peer, client bug, or coalition is required; the same pubkey-holder who is fully authorized to set the binding causes third-party fund destruction purely through ordinary transaction-ordering/timing, exactly like `syncDeps()` changing `vusd` mid-flight caused Alice's `1,000,000 VUSD` to be unrecoverable.

### Likelihood Explanation
The scenario requires ordinary, foreseeable behavior rather than an adversarial coalition: delegated-staking operators are expected to actively manage preregistrations (that is the feature's entire purpose), and depositors routinely submit EL deposit transactions that take multiple slots/epochs to be reflected as processed `DepositRequest`s on the beacon chain (`ETH1_FOLLOW_DISTANCE` plus inclusion latency). Any operator rotating or correcting a preregistration binding during that latency window — even for legitimate operational reasons — will discard in-flight deposits that do not (yet) match the new binding.

### Recommendation
- Do not silently discard a mismatched deposit request when an active preregistration exists; instead, require the deposit to be redirected/refunded through an execution-layer mechanism, or apply the deposit using the depositor's original credentials (analogous to how a top-up deposit currently preserves the validator's already-registered credentials, see `tests/.../test_incorrect_withdrawal_credentials_top_up`), rather than destroying the funds.
- Alternatively, extend `is_pending_validator`/the preregistration admission check to also block (or delay) new/changed bindings while there is a known outstanding, not-yet-processed EL deposit for the pubkey (e.g., via `deposit_requests_start_index` bookkeeping), closing the race window described above.
- At minimum, document this as an explicit, acknowledged risk to depositors of pubkeys under active or upcoming preregistration control, similar to how Hubble acknowledged reliance on admin discipline for `syncDeps()`.

### Proof of Concept
1. Depositor `D` submits an EL deposit transaction for `pubkey = P`, `withdrawal_credentials = C1`, `amount = 32 ETH` to the canonical deposit contract; no active preregistration exists for `P` at this time.
2. Operator `O`, who holds `P`'s BLS private key, broadcasts a `PreregistrationRequest(pubkey=P, withdrawal_credentials=C2, signature=...)` that gets included and processed by `process_preregistration_request` before `D`'s deposit request is processed — this succeeds because `get_active_preregistration(state, P)` is `None` and `is_pending_validator` only checks `state.pending_deposits`, which does not yet contain `D`'s deposit [6](#0-5) .
3. `D`'s `DepositRequest(pubkey=P, withdrawal_credentials=C1, amount=32 ETH, signature=...)` is later processed by `process_deposit_request`. Since `C1 != C2`, the function returns without appending to `state.pending_deposits` [7](#0-6) .
4. Result: `D`'s 32 ETH is locked in the EL deposit contract forever; no validator balance, no pending deposit, no refund path exists in-protocol.

### Citations

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

**File:** specs/_features/eip8205/beacon-chain.md (L434-461)
```markdown
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
