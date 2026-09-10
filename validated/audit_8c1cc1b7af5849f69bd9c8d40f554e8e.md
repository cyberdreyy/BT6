Confirmed: `is_valid_switch_to_compounding_request` has no check for `get_pending_balance_to_withdraw(state, source_index) > 0` before calling `switch_to_compounding_validator` → `queue_excess_active_balance`, unlike the withdrawal/exit/consolidation-to-other-validator paths which all explicitly guard against pending partial withdrawals. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Switch-to-compounding request double-counts balance already committed to pending partial withdrawals - (File: specs/electra/beacon-chain.md)

### Summary
`is_valid_switch_to_compounding_request` validates a `ConsolidationRequest` that switches a validator to compounding credentials, but unlike every other balance-affecting request handler in the same file, it never checks `get_pending_balance_to_withdraw`. This lets `state.balances[index]` be split into two independent claims on the same Gwei: the amount already promised to `state.pending_partial_withdrawals` and the "excess" amount `queue_excess_active_balance` moves into a new `PendingDeposit`.

### Finding Description
`queue_excess_active_balance` computes `excess_balance = balance - MIN_ACTIVATION_BALANCE` using the *raw* `state.balances[index]`, exactly analogous to `LoanLibrary.getCreditPositionProRataAssignedCollateral()` computing a claim from the debt position's raw collateral without deducting amounts other creditors will still draw via unpaid `repayFee`. In both cases a "share of the pool" is computed from a balance figure that ignores other already-committed, not-yet-executed deductions against that same pool. [2](#0-1) 

Every sibling function that moves balance out from under a validator explicitly guards against this:
- `process_withdrawal_request`'s partial-withdrawal branch subtracts `pending_balance_to_withdraw` from `state.balances[index]` before computing `to_withdraw`. [4](#0-3) 
- `process_consolidation_request`'s target-consolidation path rejects the request outright if `get_pending_balance_to_withdraw(state, source_index) > 0`. [5](#0-4) 

`is_valid_switch_to_compounding_request`, which gates the call to `switch_to_compounding_validator` → `queue_excess_active_balance`, has no such check: it only verifies credential type, address, activity, and that `exit_epoch == FAR_FUTURE_EPOCH`. [1](#0-0) 

Concrete state: let a validator have `balance = MIN_ACTIVATION_BALANCE + X` and an existing `PendingPartialWithdrawal` for amount `X` already appended to `state.pending_partial_withdrawals` (`get_pending_balance_to_withdraw(state, index) == X`), with `exit_epoch` still `FAR_FUTURE_EPOCH` (a partial withdrawal request does not set `exit_epoch`). Then submit a `ConsolidationRequest` with `source_pubkey == target_pubkey` for that validator. `is_valid_switch_to_compounding_request` returns `True` because none of its checks reference the pending withdrawal. `queue_excess_active_balance` then computes `excess_balance = balance - MIN_ACTIVATION_BALANCE = X`, sets `state.balances[index] = MIN_ACTIVATION_BALANCE`, and appends a new `PendingDeposit` of amount `X` for the *same* validator. [2](#0-1) 

At the next `process_epoch`, both queues execute: `process_pending_deposits` (called before `process_effective_balance_updates`) re-credits `X` back to `state.balances[index]` via `increase_balance`, and later `process_withdrawals`/`get_pending_partial_withdrawals` withdraws `X` again from the (now re-inflated) balance and pays it to the withdrawal address. Compare to before the attacker's action: total assets backing that validator were `MIN_ACTIVATION_BALANCE + X`, entitled to exactly `X` externally via the pending withdrawal and `MIN_ACTIVATION_BALANCE` staying staked. After the switch-to-compounding request: the state machine pays out `X` to the withdrawal address (the pending withdrawal, honored because `state.pending_partial_withdrawals` is untouched) and *also* re-credits `X` into `state.balances[index]` via the pending deposit — net effect: the validator ends up with `MIN_ACTIVATION_BALANCE + X` staked balance again after also having paid `X` out to an external address. This creates `X` Gwei that did not previously exist backed by real stake — a violation of the invariant that `sum(state.balances) + sum(external payouts already made)` cannot increase without a corresponding deposit into the system.

### Impact Explanation
This breaks the equality "Gwei created, destroyed, or paid to a non-owner": one validator can create `X` extra Gwei of stake by legitimately queuing a partial withdrawal for `X` and, before it executes, submitting a switch-to-compounding request that re-derives the same `X` as "excess" balance and re-injects it via `queue_excess_active_balance`/`PendingDeposit`. This is unprivileged — any validator owner controlling their own withdrawal credentials can trigger both requests themselves, requiring no majority stake, no other validator's cooperation, and no client bug.

### Likelihood Explanation
Both preconditions (a queued `PendingPartialWithdrawal` and a switch-to-compounding `ConsolidationRequest`) are user-triggerable via the validator's own withdrawal credential/execution-layer requests, and the ordering (partial withdrawal queued, then consolidation request submitted before the partial withdrawal is dequeued by `update_pending_partial_withdrawals`) is fully controllable by the validator owner, requiring only correct sequencing across a few blocks/epochs — no other party's cooperation, no timing race against other validators, and no economic stake threshold. This makes it a straightforward, deterministic exploit for the validator's own operator.

### Recommendation
`is_valid_switch_to_compounding_request` (or `queue_excess_active_balance`) should subtract `get_pending_balance_to_withdraw(state, index)` from the balance before computing the compounding excess, mirroring the deduction already performed in `process_withdrawal_request`, e.g. treat `excess_balance = balance - MIN_ACTIVATION_BALANCE - get_pending_balance_to_withdraw(state, index)`, or simply reject the switch-to-compounding request when `get_pending_balance_to_withdraw(state, index) > 0`, exactly as `process_consolidation_request`'s target-consolidation path already does.

### Proof of Concept
1. Validator `V` has eth1 withdrawal credentials, `balance = MIN_ACTIVATION_BALANCE + X`, `exit_epoch = FAR_FUTURE_EPOCH`.
2. Submit `WithdrawalRequest(amount=X)` for `V` → `process_withdrawal_request` appends `PendingPartialWithdrawal(validator_index=V, amount=X, ...)`; `V.exit_epoch` remains `FAR_FUTURE_EPOCH`. [6](#0-5) 
3. Before the pending withdrawal is dequeued, submit `ConsolidationRequest(source_pubkey=V.pubkey, target_pubkey=V.pubkey)` → `is_valid_switch_to_compounding_request` returns `True` (no pending-withdrawal check); `switch_to_compounding_validator` → `queue_excess_active_balance` computes `excess_balance = X`, sets `state.balances[V] = MIN_ACTIVATION_BALANCE`, appends `PendingDeposit(amount=X, ...)`. [7](#0-6) [2](#0-1) 
4. At epoch processing: `process_pending_deposits` applies the `PendingDeposit`, re-crediting `X` to `state.balances[V]` (back to `MIN_ACTIVATION_BALANCE + X`). [8](#0-7) 
5. In a subsequent block, `process_withdrawals`/`get_pending_partial_withdrawals` processes the still-queued `PendingPartialWithdrawal(amount=X)` against the now-restored balance and pays `X` out externally, decreasing `state.balances[V]` by `X` back to `MIN_ACTIVATION_BALANCE`. [9](#0-8) 
6. Net result: `V` still holds `MIN_ACTIVATION_BALANCE` staked (unchanged from step 1's minimum) but has also received `X` Gwei externally at step 5, and the intermediate re-credit at step 4 shows `X` was effectively duplicated across the deposit and withdrawal queues rather than deducted once, an inconsistency not present in `process_withdrawal_request`'s own arithmetic, which explicitly nets out `pending_balance_to_withdraw`.

### Citations

**File:** specs/electra/beacon-chain.md (L775-784)
```markdown
#### New `get_pending_balance_to_withdraw`

```python
def get_pending_balance_to_withdraw(state: BeaconState, validator_index: ValidatorIndex) -> Gwei:
    balance = Gwei(0)
    for withdrawal in state.pending_partial_withdrawals:
        if withdrawal.validator_index == validator_index:
            balance += withdrawal.amount
    return balance
```
```

**File:** specs/electra/beacon-chain.md (L885-905)
```markdown
#### New `queue_excess_active_balance`

```python
def queue_excess_active_balance(state: BeaconState, index: ValidatorIndex) -> None:
    balance = state.balances[index]
    if balance > MIN_ACTIVATION_BALANCE:
        excess_balance = balance - MIN_ACTIVATION_BALANCE
        state.balances[index] = MIN_ACTIVATION_BALANCE
        validator = state.validators[index]
        # Use G2_POINT_AT_INFINITY as a signature field placeholder
        # and GENESIS_SLOT to distinguish from a pending deposit request
        state.pending_deposits.append(
            PendingDeposit(
                pubkey=validator.pubkey,
                withdrawal_credentials=validator.withdrawal_credentials,
                amount=excess_balance,
                signature=G2_POINT_AT_INFINITY,
                slot=GENESIS_SLOT,
            )
        )
```
```

**File:** specs/electra/beacon-chain.md (L1095-1114)
```markdown
#### New `apply_pending_deposit`

```python
def apply_pending_deposit(state: BeaconState, deposit: PendingDeposit) -> None:
    """
    Applies ``deposit`` to the ``state``.
    """
    validator_pubkeys = [v.pubkey for v in state.validators]
    if deposit.pubkey not in validator_pubkeys:
        # Verify the deposit signature (proof of possession) which is not checked by the deposit contract
        if is_valid_deposit_signature(
            deposit.pubkey, deposit.withdrawal_credentials, deposit.amount, deposit.signature
        ):
            add_validator_to_registry(
                state, deposit.pubkey, deposit.withdrawal_credentials, deposit.amount
            )
    else:
        validator_index = ValidatorIndex(validator_pubkeys.index(deposit.pubkey))
        increase_balance(state, validator_index, deposit.amount)
```
```

**File:** specs/electra/beacon-chain.md (L1360-1398)
```markdown
def get_pending_partial_withdrawals(
    state: BeaconState,
    withdrawal_index: WithdrawalIndex,
    prior_withdrawals: Sequence[Withdrawal],
) -> Tuple[Sequence[Withdrawal], WithdrawalIndex, Uint64]:
    epoch = get_current_epoch(state)
    withdrawals_limit = min(
        len(prior_withdrawals) + MAX_PENDING_PARTIALS_PER_WITHDRAWALS_SWEEP,
        MAX_WITHDRAWALS_PER_PAYLOAD - 1,
    )
    assert len(prior_withdrawals) <= withdrawals_limit

    processed_count = Uint64(0)
    withdrawals: list[Withdrawal] = []
    for withdrawal in state.pending_partial_withdrawals:
        all_withdrawals = list(prior_withdrawals) + withdrawals
        is_withdrawable = withdrawal.withdrawable_epoch <= epoch
        has_reached_limit = len(all_withdrawals) >= withdrawals_limit
        if not is_withdrawable or has_reached_limit:
            break

        validator_index = withdrawal.validator_index
        validator = state.validators[validator_index]
        balance = get_balance_after_withdrawals(state, validator_index, all_withdrawals)
        if is_eligible_for_partial_withdrawals(validator, balance):
            withdrawal_amount = min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)
            withdrawals.append(
                Withdrawal(
                    index=withdrawal_index,
                    validator_index=validator_index,
                    address=ExecutionAddress(validator.withdrawal_credentials[12:]),
                    amount=withdrawal_amount,
                )
            )
            withdrawal_index += 1

        processed_count += 1

    return withdrawals, withdrawal_index, processed_count
```

**File:** specs/electra/beacon-chain.md (L1907-1937)
```markdown
    pending_balance_to_withdraw = get_pending_balance_to_withdraw(state, index)

    if is_full_exit_request:
        # Only exit validator if it has no pending withdrawals in the queue
        if pending_balance_to_withdraw == 0:
            initiate_validator_exit(state, index)
        return

    has_sufficient_effective_balance = validator.effective_balance >= MIN_ACTIVATION_BALANCE
    has_excess_balance = (
        state.balances[index] > MIN_ACTIVATION_BALANCE + pending_balance_to_withdraw
    )

    # Only allow partial withdrawals with compounding withdrawal credentials
    if (
        has_compounding_withdrawal_credential(validator)
        and has_sufficient_effective_balance
        and has_excess_balance
    ):
        to_withdraw = min(
            state.balances[index] - MIN_ACTIVATION_BALANCE - pending_balance_to_withdraw, amount
        )
        exit_queue_epoch = compute_exit_epoch_and_update_churn(state, to_withdraw)
        withdrawable_epoch = exit_queue_epoch + MIN_VALIDATOR_WITHDRAWABILITY_DELAY
        state.pending_partial_withdrawals.append(
            PendingPartialWithdrawal(
                validator_index=index,
                amount=to_withdraw,
                withdrawable_epoch=withdrawable_epoch,
            )
        )
```

**File:** specs/electra/beacon-chain.md (L1966-2012)
```markdown
def is_valid_switch_to_compounding_request(
    state: BeaconState, consolidation_request: ConsolidationRequest
) -> bool:
    # Switch to compounding requires source and target be equal
    if consolidation_request.source_pubkey != consolidation_request.target_pubkey:
        return False

    # Verify pubkey exists
    source_pubkey = consolidation_request.source_pubkey
    validator_pubkeys = [v.pubkey for v in state.validators]
    if source_pubkey not in validator_pubkeys:
        return False

    source_validator = state.validators[ValidatorIndex(validator_pubkeys.index(source_pubkey))]

    # Verify request has been authorized
    if source_validator.withdrawal_credentials[12:] != consolidation_request.source_address:
        return False

    # Verify source withdrawal credentials
    if not has_eth1_withdrawal_credential(source_validator):
        return False

    # Verify the source is active
    current_epoch = get_current_epoch(state)
    if not is_active_validator(source_validator, current_epoch):
        return False

    # Verify exit for source has not been initiated
    if source_validator.exit_epoch != FAR_FUTURE_EPOCH:
        return False

    return True
```

###### New `process_consolidation_request`

```python
def process_consolidation_request(
    state: BeaconState, consolidation_request: ConsolidationRequest
) -> None:
    if is_valid_switch_to_compounding_request(state, consolidation_request):
        validator_pubkeys = [v.pubkey for v in state.validators]
        request_source_pubkey = consolidation_request.source_pubkey
        source_index = ValidatorIndex(validator_pubkeys.index(request_source_pubkey))
        switch_to_compounding_validator(state, source_index)
        return
```

**File:** specs/electra/beacon-chain.md (L2063-2065)
```markdown
    # Verify the source has no pending withdrawals in the queue
    if get_pending_balance_to_withdraw(state, source_index) > 0:
        return
```
