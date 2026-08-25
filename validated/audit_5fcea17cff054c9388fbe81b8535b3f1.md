Confirmed: `nonce::state::Data` does not store a `rent_exempt_reserve` field (unlike stake's `Meta` struct which does) [1](#0-0) . The nonce withdrawal path always recomputes the minimum balance from the *current* `Rent` sysvar rather than any value captured at initialization time [2](#0-1) , and this codebase demonstrably supports governance/feature-driven changes to the rent rate that take effect network-wide at epoch boundaries [3](#0-2) . This is a solid analog to the Superfluid bug class.

### Title
Partial nonce withdrawals can unexpectedly revert (or silently allow over-collateralization) because `withdraw_nonce_account` recomputes the rent-exempt reserve from the current `Rent` sysvar instead of the value in effect at account funding time - (File: programs/system/src/system_instruction.rs)

### Summary
`withdraw_nonce_account()` recalculates the minimum required reserve for a partial nonce withdrawal using the *current* `Rent` sysvar (`rent.minimum_balance(...)`) at the moment the `WithdrawNonceAccount` instruction executes, rather than using a value fixed at the time the nonce account was funded/initialized. Because `lamports_per_byte` (an on-chain, governance/feature-activation-controlled parameter) can change during the network's lifetime via feature-gated rent adjustments processed at epoch boundaries, a nonce account that was correctly funded to be rent-exempt under the rent parameters in effect when it was created may fail to satisfy a routine partial withdrawal after those parameters change — exactly the "recompute deposit with stale/changed governance parameters" bug class described in the Superfluid report.

### Finding Description
`withdraw_nonce_account` handles the "partial withdraw" branch (when `lamports != from.get_lamports()`) by computing:

```rust
let min_balance = rent.minimum_balance(from.get_data().len());
let amount = checked_add(lamports, min_balance)?;
if amount > from.get_lamports() {
    return Err(InstructionError::InsufficientFunds);
}
``` [2](#0-1) 

This `rent` is the *current* `Rent` sysvar passed in from the bank at the time the instruction executes, not a value that was recorded when the nonce account was created via `initialize_nonce_account`, which itself validates against the current rent at that (earlier) time:
```rust
let min_balance = rent.minimum_balance(account.get_data().len());
if account.get_lamports() < min_balance {
    return Err(InstructionError::InsufficientFunds);
}
``` [4](#0-3) 

Unlike the Stake program's `Meta` struct, which stores `rent_exempt_reserve` at account-creation time so that later relies on a fixed original value [5](#0-4) , `nonce::state::Data` has no analogous stored reserve field — it only tracks `authority`, `durable_nonce`/`blockhash`, and `fee_calculator` [1](#0-0) . The withdraw path therefore always derives the required reserve dynamically from whatever `Rent` sysvar is active at withdrawal time.

Crucially, this codebase confirms that the rent rate (`lamports_per_byte`) is *not* immutable — it is adjusted via a sequence of feature-gated governance actions applied at each epoch boundary in `compute_and_apply_new_feature_activations`:
```rust
for (feature_id, lamports_per_byte) in rent_feature_gates {
    if new_feature_activations.contains(&feature_id) {
        self.rent_collector.rent.lamports_per_byte = lamports_per_byte;
        self.update_rent();
    }
}
``` [6](#0-5) 
and there is a dedicated regression test, `test_rent_feature_gates_epoch_transition`, exercising multiple such rent-rate changes across epoch transitions [7](#0-6) . This is functionally identical to the Superfluid report's premise that "governance parameters... can be changed at any time" after a deposit was calculated — here, `lamports_per_byte` plays the role of the Superfluid `SUPERTOKEN_MINIMUM_DEPOSIT_KEY`/liquidation-period parameters, and the recomputed `min_balance` in `withdraw_nonce_account` plays the role of the recomputed `initialDeposit` in `cancelProgram()`.

### Impact Explanation
If `lamports_per_byte` (and thus `rent.minimum_balance(NonceState::size())`) increases after a nonce account was funded and initialized, a previously-valid partial withdrawal amount that the nonce authority computed based on the account's original rent-exempt reserve will now fail with `InstructionError::InsufficientFunds`, even though the account balance and requested withdrawal were correctly sized against the rent parameters at funding time. This causes legitimate `WithdrawNonceAccount` transactions to revert unexpectedly (broken user-facing invariant / unpredictable accounting, matching the "transaction revert" impact class from the report), and forces nonce-account owners to over-collateralize beyond what they originally funded in order to keep any future partial withdrawal capacity. Full closures (`lamports == from.get_lamports()`) are unaffected since they bypass the `min_balance` check entirely, so the issue is scoped to partial withdrawals.

This is a lower-severity analog than the original (fund custody/consensus impact is limited to reverted transactions rather than fund loss or consensus divergence), but it is a concretely reachable path from an ordinary user's `WithdrawNonceAccount` instruction combined with a network-level, already-implemented rent-adjustment mechanism.

### Likelihood Explanation
The rent-rate change mechanism (`rent_feature_gates`) is real, already implemented, and tested in this codebase — it is not a hypothetical/privileged action but a standard feature-activation path that occurs automatically at epoch boundaries once features are activated on the cluster. Any nonce account funded before such an activation and subject to a partial withdrawal after the activation will hit this path. Likelihood is moderate: it requires a rent-rate-changing feature to actually activate on the cluster (a rare, infrequent, network-wide event), but when it happens, all outstanding nonce accounts are affected without any attacker action required.

### Recommendation
Either (a) store the `rent_exempt_reserve` (or minimum balance) computed at `initialize_nonce_account` time inside `nonce::state::Data`, and have `withdraw_nonce_account` compare against that stored value instead of recomputing from the current `Rent` sysvar, mirroring the Stake program's `Meta::rent_exempt_reserve` approach [5](#0-4) ; or (b) explicitly document/accept that nonce accounts must be re-topped-up after any rent-rate change, and provide a migration/adjustment mechanism analogous to `adjust_delegation_for_rent` used for stake accounts under SIMD-0392 [8](#0-7) .

### Proof of Concept
1. Create and fund a nonce account with exactly `rent.minimum_balance(NonceState::size()) + N` lamports under the current rent parameters, then call `InitializeNonceAccount` (succeeds, per `initialize_nonce_account`) [9](#0-8) .
2. Activate a `set_lamports_per_byte_to_*` feature that raises `lamports_per_byte`, causing an epoch-boundary rent update as in `test_rent_feature_gates_epoch_transition` [7](#0-6) .
3. Submit `WithdrawNonceAccount` for `N` lamports (a partial withdrawal that would have succeeded under the original rent rate). It now fails with `InstructionError::InsufficientFunds` because `rent.minimum_balance(...)` is recomputed with the new, higher rate [2](#0-1) , demonstrating the stale-vs-current-parameter mismatch and resulting transaction revert.

### Citations

**File:** account-decoder/src/parse_nonce.rs (L19-24)
```rust
        State::Initialized(data) => Ok(UiNonceState::Initialized(UiNonceData {
            authority: data.authority.to_string(),
            blockhash: data.blockhash().to_string(),
            fee_calculator: data.fee_calculator.into(),
        })),
    }
```

**File:** programs/system/src/system_instruction.rs (L138-150)
```rust
            } else {
                let min_balance = rent.minimum_balance(from.get_data().len());
                let amount = checked_add(lamports, min_balance)?;
                if amount > from.get_lamports() {
                    ic_msg!(
                        invoke_context,
                        "Withdraw nonce account: insufficient lamports {}, need {}",
                        from.get_lamports(),
                        amount,
                    );
                    return Err(InstructionError::InsufficientFunds);
                }
                check_signer(&data.authority)?;
```

**File:** programs/system/src/system_instruction.rs (L178-189)
```rust
    match account.get_state::<Versions>()?.state() {
        State::Uninitialized => {
            let min_balance = rent.minimum_balance(account.get_data().len());
            if account.get_lamports() < min_balance {
                ic_msg!(
                    invoke_context,
                    "Initialize nonce account: insufficient lamports {}, need {}",
                    account.get_lamports(),
                    min_balance
                );
                return Err(InstructionError::InsufficientFunds);
            }
```

**File:** runtime/src/bank.rs (L6154-6186)
```rust
        // SIMD-0437 feature gates: all assume rent exemption threshold has been deprecated
        // (SIMD-0194), so rent.lamports_per_byte can be set directly. These gates are
        // expected to activate in order; if multiple activate in one epoch, the lowest
        // activated lamports_per_byte value will be used. If features are activated out of
        // order, the most recently activated value will be used.
        let rent_feature_gates = [
            (
                feature_set::set_lamports_per_byte_to_6333::id(),
                feature_set::set_lamports_per_byte_to_6333::LAMPORTS_PER_BYTE,
            ),
            (
                feature_set::set_lamports_per_byte_to_5080::id(),
                feature_set::set_lamports_per_byte_to_5080::LAMPORTS_PER_BYTE,
            ),
            (
                feature_set::set_lamports_per_byte_to_2575::id(),
                feature_set::set_lamports_per_byte_to_2575::LAMPORTS_PER_BYTE,
            ),
            (
                feature_set::set_lamports_per_byte_to_1322::id(),
                feature_set::set_lamports_per_byte_to_1322::LAMPORTS_PER_BYTE,
            ),
            (
                feature_set::set_lamports_per_byte_to_696::id(),
                feature_set::set_lamports_per_byte_to_696::LAMPORTS_PER_BYTE,
            ),
        ];
        for (feature_id, lamports_per_byte) in rent_feature_gates {
            if new_feature_activations.contains(&feature_id) {
                self.rent_collector.rent.lamports_per_byte = lamports_per_byte;
                self.update_rent();
            }
        }
```

**File:** runtime/src/stake_utils.rs (L44-53)
```rust
    let rent_exempt_reserve = rent.minimum_balance(stake_account.data().len());
    let stake_amount = lamports
        .checked_sub(rent_exempt_reserve)
        .expect("lamports >= rent_exempt_reserve");

    let meta = Meta {
        authorized: Authorized::auto(authorized),
        #[expect(deprecated)]
        rent_exempt_reserve,
        ..Meta::default()
```

**File:** runtime/src/bank/tests.rs (L7105-7169)
```rust
#[test]
fn test_rent_feature_gates_epoch_transition() {
    let (mut genesis_config, _mint_keypair) = create_genesis_config(1_000_000);
    genesis_config.rent.lamports_per_byte = 0;
    let (mut bank, bank_forks) = Bank::new_with_bank_forks_for_tests(&genesis_config);

    let rent_feature_gates = [
        (
            feature_set::set_lamports_per_byte_to_6333::id(),
            feature_set::set_lamports_per_byte_to_6333::LAMPORTS_PER_BYTE,
        ),
        (
            feature_set::set_lamports_per_byte_to_5080::id(),
            feature_set::set_lamports_per_byte_to_5080::LAMPORTS_PER_BYTE,
        ),
        (
            feature_set::set_lamports_per_byte_to_2575::id(),
            feature_set::set_lamports_per_byte_to_2575::LAMPORTS_PER_BYTE,
        ),
        (
            feature_set::set_lamports_per_byte_to_1322::id(),
            feature_set::set_lamports_per_byte_to_1322::LAMPORTS_PER_BYTE,
        ),
        (
            feature_set::set_lamports_per_byte_to_696::id(),
            feature_set::set_lamports_per_byte_to_696::LAMPORTS_PER_BYTE,
        ),
        (
            feature_set::set_lamports_per_byte_to_6960::id(),
            feature_set::set_lamports_per_byte_to_6960::LAMPORTS_PER_BYTE,
        ),
    ];
    let feature_account_balance =
        std::cmp::max(genesis_config.rent.minimum_balance(Feature::size_of()), 1);

    for (feature_id, expected_lamports_per_byte) in rent_feature_gates {
        assert!(
            !bank.feature_set.is_active(&feature_id),
            "feature should be inactive before activation"
        );
        bank.store_account(
            &feature_id,
            &feature::create_account(&Feature { activated_at: None }, feature_account_balance),
        );

        // Cross the epoch boundary to apply feature activation.
        goto_end_of_slot(bank.clone());
        bank = new_from_parent_next_epoch(bank, &bank_forks, 1);

        assert!(
            bank.feature_set.is_active(&feature_id),
            "feature should be active after epoch transition"
        );
        assert_eq!(
            bank.rent_collector.rent.lamports_per_byte, expected_lamports_per_byte,
            "rent collector should reflect the active gate"
        );

        let rent_account = bank.get_account(&sysvar::rent::id()).unwrap();
        let rent = from_account::<sysvar::rent::Rent>(&rent_account).unwrap();
        assert_eq!(
            rent.lamports_per_byte, expected_lamports_per_byte,
            "rent sysvar should be updated after activation"
        );
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L49-76)
```rust
/// Adjusts stake delegation based on Rent sysvar parameters.
///
/// As part of SIMD-0392, if Rent is ever increased, we need to make sure that
/// lamports are not double-counted for the rent-exempt minimum and the stake
/// delegation. This function adjusts the delegation in a Stake if needed, right
/// at distribution time.
fn adjust_delegation_for_rent(
    delegation: &mut Delegation,
    rewarded_epoch: Epoch,
    new_delegation_with_rewards: u64,
    lamports_with_rewards: u64,
    minimum_lamports: u64,
) {
    let new_delegation = std::cmp::min(
        new_delegation_with_rewards,
        lamports_with_rewards.saturating_sub(minimum_lamports),
    );

    if new_delegation != delegation.stake {
        delegation.stake = new_delegation;
        // Deactivate stake if needed. This deactivation is immediate,
        // unlike a requested deactivation which happens at the next epoch
        // boundary
        if new_delegation == 0 {
            delegation.deactivation_epoch = rewarded_epoch;
        }
    }
}
```
