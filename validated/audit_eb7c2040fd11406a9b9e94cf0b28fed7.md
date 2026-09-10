### Title
Builder withdrawal amount is minted unclamped on the EL side while `apply_withdrawals` only debits `min(withdrawal.amount, builder_balance)` from `state.builders[...].balance` - (File: specs/gloas/beacon-chain.md)

### Summary
In the Gloas builder-payment/withdrawal flow, a `BuilderPendingWithdrawal.amount` is queued at payment-settlement time and later turned into an execution-layer `Withdrawal` whose `amount` field is copied verbatim (unclamped) from the queued entry. When that withdrawal is finally applied to consensus-layer state, the balance decrement is clamped to the builder's *current* balance (`min(withdrawal.amount, builder_balance)`), but the `Withdrawal` object handed to the execution layer for minting still carries the original, unclamped `amount`. This is structurally identical to the reported Lido bug: a "requested" amount is recorded/committed (`lidoLockedETH` / `withdrawal.amount`), the actually-applied amount is clamped to what's really available, and the two are allowed to diverge, breaking a conservation invariant.

### Finding Description
`settle_builder_payment` appends a `BuilderPendingWithdrawal` with a fixed `amount` to `state.builder_pending_withdrawals`: [1](#0-0) 

Later, `get_builder_withdrawals` turns each queued entry into an execution-layer `Withdrawal` whose `amount` is copied straight from the pending withdrawal, with no clamping to the builder's current balance: [2](#0-1) 

That unmodified `Withdrawal` list becomes `state.payload_expected_withdrawals`, which the execution layer is required to honor and mint against (per the spec's own note that "the execution layer mints the full committed amount regardless"): [3](#0-2) 

Meanwhile, the consensus-layer side of the same withdrawal only reduces `state.builders[builder_index].balance` by `min(withdrawal.amount, builder_balance)`: [4](#0-3) 

If a builder's on-chain `balance` is lower than the sum of its still-queued `builder_pending_withdrawals` amounts at the moment those withdrawals are dequeued (e.g., because multiple pending withdrawals were queued for the same builder and an earlier one already drained most of the balance, or the builder's balance was otherwise reduced between queuing and processing), then for the shortfall entry: the CL only deducts the builder's remaining balance (saturating, per `min(...)`), but the EL still mints the full, unclamped `withdrawal.amount` recorded in `payload_expected_withdrawals`. The spec text itself already acknowledges this exact class of risk for validator withdrawals and specifically restructured `process_withdrawals` to apply balance deductions immediately to avoid "net supply inflation" from `decrease_balance` saturation — but the identical saturating `min()` pattern for builder withdrawals in `apply_withdrawals` was not given the same unclamped-vs-clamped amount reconciliation: the `Withdrawal.amount` sent to the EL is never re-derived from the actual clamped/deducted value, so the note's stated invariant ("any CL-side saturation creates a net supply inflation") is violated for builders specifically.

### Impact Explanation
This breaks the equality "Gwei removed from `state.builders[*].balance` == Gwei minted to the withdrawal address on the execution layer." Whenever the clamp in `apply_withdrawals` triggers for a builder withdrawal (`withdrawal.amount > builder_balance`), ETH is minted on the EL side that is not backed by any corresponding decrease of `state.builders[builder_index].balance`. This is a direct case of "Gwei created... paid to a non-owner" (the withdrawal's `fee_recipient`), which under the given rules maps to Critical severity (protocol-level Gwei creation), not merely an accounting/UX issue like the original Lido report.

### Likelihood Explanation
This requires no malicious peer, coalition, or client bug — it is a pure spec-level accounting gap reachable whenever a builder accumulates more than one `BuilderPendingWithdrawal` (e.g., multiple payment settlements via `settle_builder_payment` before the queue drains) such that the builder's `balance` at processing time is less than the sum of its still-queued withdrawal amounts. Because `builder_pending_withdrawals` is a FIFO list processed a limited number of entries per block (`MAX_WITHDRAWALS_PER_PAYLOAD - 1`), a backlog can accumulate across blocks, making the shortfall condition plausible under normal payment/withdrawal traffic rather than a contrived edge case.

### Recommendation
Have `apply_withdrawals` compute the clamped/actual amount first, and use that same clamped value both for the balance deduction and for what is treated as "minted"/committed for supply-accounting purposes — e.g., either (a) clamp `withdrawal.amount` to the builder's balance at the point `get_builder_withdrawals` constructs the `Withdrawal` (mirroring how `get_pending_partial_withdrawals` clamps `withdrawal_amount = min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)` for validators), so the committed EL payload amount can never exceed what CL actually removes, or (b) guarantee at `settle_builder_payment`/payment-accrual time that a builder can never accrue pending withdrawal obligations exceeding its `balance`, preserving the invariant the spec's own commentary already assumes for validators.

### Proof of Concept
Conceptual state trace (spec-level, no client/EL bug required):
1. Builder `B` has `state.builders[B].balance = 10 ETH`.
2. Two payments settle for `B` via `settle_builder_payment`, each appending a `BuilderPendingWithdrawal(builder_index=B, amount=8 ETH, ...)`, for a total of `16 ETH` queued while `balance` is only `10 ETH` [1](#0-0) .
3. Block N processes the first pending withdrawal via `get_builder_withdrawals`: `Withdrawal(amount=8 ETH)` is added to `payload_expected_withdrawals`; `apply_withdrawals` deducts `min(8, 10) = 8 ETH`, leaving `balance = 2 ETH` [4](#0-3) . EL mints `8 ETH` — matches, no drift yet.
4. Block N+1 processes the second pending withdrawal: `get_builder_withdrawals` again emits `Withdrawal(amount=8 ETH)` unchanged from the queued entry [5](#0-4) ; `apply_withdrawals` deducts only `min(8, 2) = 2 ETH`, leaving `balance = 0` [4](#0-3) .
5. Per the spec's own note, the execution layer "mints the full committed amount regardless" of CL-side saturation [6](#0-5)  — i.e., the EL mints `8 ETH` to the withdrawal's `fee_recipient` in block N+1, while the CL only removed `2 ETH` from `state.builders[B].balance`.
6. Net result: `6 ETH` were minted on the execution layer with no corresponding decrease anywhere in consensus-layer state — Gwei created from nothing.

### Citations

**File:** specs/gloas/beacon-chain.md (L1518-1527)
```markdown
#### New `settle_builder_payment`

```python
def settle_builder_payment(state: BeaconState, payment_index: Uint64) -> None:
    assert payment_index < len(state.builder_pending_payments)
    payment = state.builder_pending_payments[payment_index]
    if payment.withdrawal.amount > 0:
        state.builder_pending_withdrawals.append(payment.withdrawal)
    state.builder_pending_payments[payment_index] = BuilderPendingPayment.empty()
```
```

**File:** specs/gloas/beacon-chain.md (L1802-1834)
```markdown
##### New `get_builder_withdrawals`

```python
def get_builder_withdrawals(
    state: BeaconState,
    withdrawal_index: WithdrawalIndex,
    prior_withdrawals: Sequence[Withdrawal],
) -> Tuple[Sequence[Withdrawal], WithdrawalIndex, Uint64]:
    withdrawals_limit = MAX_WITHDRAWALS_PER_PAYLOAD - 1
    assert len(prior_withdrawals) <= withdrawals_limit

    processed_count = Uint64(0)
    withdrawals: list[Withdrawal] = []
    for withdrawal in state.builder_pending_withdrawals:
        all_withdrawals = list(prior_withdrawals) + withdrawals
        has_reached_limit = len(all_withdrawals) >= withdrawals_limit
        if has_reached_limit:
            break

        builder_index = withdrawal.builder_index
        withdrawals.append(
            Withdrawal(
                index=withdrawal_index,
                validator_index=convert_builder_index_to_validator_index(builder_index),
                address=withdrawal.fee_recipient,
                amount=withdrawal.amount,
            )
        )
        withdrawal_index += 1
        processed_count += 1

    return withdrawals, withdrawal_index, processed_count
```
```

**File:** specs/gloas/beacon-chain.md (L1922-1932)
```markdown
```python
def apply_withdrawals(state: BeaconState, withdrawals: Sequence[Withdrawal]) -> None:
    for withdrawal in withdrawals:
        # [Modified in Gloas:EIP7732]
        if is_builder_index(withdrawal.validator_index):
            builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
            builder_balance = state.builders[builder_index].balance
            state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
        else:
            decrease_balance(state, withdrawal.validator_index, withdrawal.amount)
```
```

**File:** specs/gloas/beacon-chain.md (L1967-1990)
```markdown
##### Modified `process_withdrawals`

*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
`process_parent_execution_payload` (which updates `state.latest_block_hash`) and
before `process_execution_payload_bid` as the latter function affects validator
balances.

*Note*: Unlike deposits (which are applied at the child's slot via
`apply_parent_execution_payload`), withdrawal balance deductions are applied
immediately via `apply_withdrawals`. Deferring the deduction to the child's slot
would break the total supply invariant: state transitions between the commitment
slot and the deduction slot (e.g., `process_pending_consolidations` at an epoch
boundary) can reduce a validator's balance below the committed withdrawal
amount, causing `decrease_balance` to saturate at zero. Since the execution
layer mints the full committed amount regardless, any CL-side saturation creates
a net supply inflation. As a consequence, `state.balances` reflects the
withdrawal deduction before the corresponding execution payload is confirmed,
creating a transient asymmetry with the EL state at `state.latest_block_hash`.
Off-chain consumers that require CL/EL balance consistency can reconstruct
pre-deduction balances by adding back `state.payload_expected_withdrawals`.

```
