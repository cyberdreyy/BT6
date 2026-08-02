## Finding

### Title
`staking_contract::set_beneficiary_for_operator` allows an unregistered/opted-out beneficiary that permanently blocks `distribute` and `switch_operator` for all other shareholders - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::set_beneficiary_for_operator` lets an operator set an arbitrary `new_beneficiary` address with no check that the address exists or accepts APT, unlike the analogous `vesting::set_beneficiary`, which explicitly enforces `assert_account_is_registered_for_apt` for exactly this reason. [1](#0-0) [2](#0-1) 

### Finding Description
`distribute_internal` iterates over every shareholder of the shared `distribution_pool` (staker, current operator/its beneficiary, and possibly past operators recorded via `switch_operator`) in a single loop and calls `aptos_account::deposit_coins` for each recipient: [3](#0-2) 

`aptos_account::deposit_coins` will **abort** with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the target account is not registered for `AptosCoin` and has explicitly opted out of unregistered/direct coin transfers via `set_allow_direct_coin_transfers(false)`: [4](#0-3) 

Because a Move abort unwinds the entire transaction, if the operator's beneficiary (set via `set_beneficiary_for_operator`, with no registration check) is such an account, the abort inside the loop reverts the whole `distribute_internal` call — not just the operator's payout — blocking payout to every other shareholder in the same `distribution_pool` (the staker, and any prior operators still owed a pending distribution).

The vesting module's own developer comment on the analogous `set_beneficiary` function makes the intended invariant explicit: a beneficiary must be verified to receive APT "so `distribute()` wouldn't fail and block all other accounts from receiving APT if one beneficiary is not registered." [2](#0-1) 
`staking_contract::set_beneficiary_for_operator` violates this same invariant that the codebase itself considers a requirement, since it performs no equivalent check.

This is compounded by `switch_operator`, which force-calls `distribute_internal` before allowing the staker to move to a new operator: [5](#0-4) 
If `distribute_internal` always aborts because the current operator's beneficiary cannot receive coins, the staker is permanently unable to call `switch_operator` to leave that operator, and unlocked/inactive stake sitting in the pool's distribution queue becomes stranded until the operator (who controls the malicious/misconfigured beneficiary) fixes it — something the staker cannot force.

### Impact Explanation
- Operator-caused (or operator-beneficiary self-inflicted) misconfiguration can permanently trap the staker's already-unlocked/inactive stake and any pending commission owed to other parties sharing the `distribution_pool`, since `distribute` reverts entirely rather than skipping the bad recipient.
- It blocks `switch_operator`/`switch_operator_with_same_commission`, stripping the staker of the ability to change operator — a wrong-role/stuck-control condition where the staker (owner of the funds) loses their normal exit path due to an unprivileged operator action.
- No privileged action is required to trigger it: an operator (unprivileged relative to the staker's stake) can call `set_beneficiary_for_operator` with any address, including one that later (or already) has direct-transfer acceptance disabled.

### Likelihood Explanation
The precondition (`can_receive_direct_coin_transfers` returning false) requires either the beneficiary account to have called `set_allow_direct_coin_transfers(false)` and never registered a `CoinStore`/`FungibleStore` for `AptosCoin`, or be a contract/resource account without transfer support. This is a normal, reachable end-user setting (not a privileged assumption), and can be set at any time after `set_beneficiary_for_operator` is called (i.e., even if the beneficiary was fine when set, it can be flipped later to grief the staker). This makes the condition realistically triggerable by any operator wanting to grief a staker, or by accident.

### Recommendation
Add the same safeguard used in `vesting::set_beneficiary`: require `aptos_account::assert_account_is_registered_for_apt(new_beneficiary)` in `staking_contract::set_beneficiary_for_operator`, and/or make `distribute_internal`'s payout loop resilient to a single failing recipient (e.g., skip/queue that recipient's amount instead of aborting the whole distribution), so a single non-compliant recipient cannot block payouts to unrelated shareholders or block `switch_operator`.

### Proof of Concept
1. Staker creates a staking contract with `operator_1` via `create_staking_contract`, contract accrues rewards/commission. [6](#0-5) 
2. `operator_1` calls `set_beneficiary_for_operator(operator_1, beneficiary_addr)` where `beneficiary_addr` is an account that has called `aptos_account::set_allow_direct_coin_transfers(false)` and has no `AptosCoin` store — no check rejects this. [7](#0-6) 
3. Staker requests commission, unlocks stake, and later fast-forwards to unlock — mirrors existing test flow. [8](#0-7) 
4. Any call to `distribute(staker, operator_1)` reaches the payout loop; when `recipient == operator_1`, it resolves to `beneficiary_addr` and `aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`, reverting the whole `distribute_internal` call (all other shareholders' payouts also revert). [3](#0-2) [9](#0-8) 
5. Staker attempts `switch_operator(staker, operator_1, operator_2, ...)` to escape; this internally forces `distribute_internal`, which also aborts, so the staker cannot switch away from `operator_1` while stake remains stuck. [10](#0-9) 

Note: I was not able to fully trace whether `unlock_stake`/`request_commission` in `staking_contract.move` also invoke `distribute_internal` directly (index/search access was exhausted before confirming line-level call sites for those two functions), so the full blast radius (beyond `distribute` and `switch_operator`, which are confirmed) is not exhaustively verified — the confirmed impact on `distribute` and `switch_operator` alone is sufficient to demonstrate the stranded-funds/loss-of-exit issue.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L781-805)
```text
        );

        let (_, staking_contract) = staking_contracts.remove(&old_operator);
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            old_operator,
            &mut staking_contract,
        );

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        request_commission_internal(
            old_operator,
            &mut staking_contract,
        );

        // Update the staking contract's commission rate and stake pool's operator.
        stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator);
        staking_contract.commission_percentage = new_commission_percentage;

        let pool_address = staking_contract.pool_address;
        staking_contracts.add(new_operator, staking_contract);
        emit(SwitchOperator { pool_address, old_operator, new_operator });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-838)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
    public entry fun set_beneficiary_for_operator(
        operator: &signer, new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        assert!(
            features::operator_beneficiary_change_enabled(),
            std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
        );
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator =
                new_beneficiary;
        } else {
            move_to(
                operator,
                BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary }
            );
        };

        emit(
            SetBeneficiaryForOperator {
                operator: operator_addr,
                old_beneficiary,
                new_beneficiary
            }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-911)
```text
        // Buy all recipients out of the distribution pool.
        while (distribution_pool.shareholders_count() > 0) {
            let recipients = distribution_pool.shareholders();
            let recipient = recipients[0];
            let current_shares = distribution_pool.shares(recipient);
            let amount_to_distribute =
                distribution_pool.redeem_shares(recipient, current_shares);
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
            aptos_account::deposit_coins(
                recipient, coin::extract(&mut coins, amount_to_distribute)
            );

            emit(
                Distribute {
                    operator,
                    pool_address,
                    recipient,
                    amount: amount_to_distribute
                }
            );
        };
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-927)
```text
    public entry fun set_beneficiary(
        admin: &signer,
        contract_address: address,
        shareholder: address,
        new_beneficiary: address,
    ) acquires VestingContract {
        // Verify that the beneficiary account is set up to receive APT. This is a requirement so distribute() wouldn't
        // fail and block all other accounts from receiving APT if one beneficiary is not registered.
        assert_account_is_registered_for_apt(new_beneficiary);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);

```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L111-131)
```text
    public fun deposit_coins<CoinType>(
        to: address, coins: Coin<CoinType>
    ) acquires DirectTransferConfig {
        if (!account::exists_at(to)) {
            create_account(to);
            spec {
                // TODO(fa_migration)
                // assert coin::spec_is_account_registered<AptosCoin>(to);
                // assume aptos_std::type_info::type_of<CoinType>() == aptos_std::type_info::type_of<AptosCoin>() ==>
                //     coin::spec_is_account_registered<CoinType>(to);
            };
        };
        if (!coin::is_account_registered<CoinType>(to)) {
            assert!(
                can_receive_direct_coin_transfers(to),
                error::permission_denied(EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS)
            );
            coin::register<CoinType>(&create_signer(to));
        };
        coin::deposit<CoinType>(to, coins)
    }
```

**File:** aptos-move/e2e-move-tests/src/tests/stake.rs (L343-366)
```rust
#[test]
fn test_staking_contract() {
    let mut harness = MoveHarness::new();
    let staker = harness.new_account_at(AccountAddress::from_hex_literal("0x11").unwrap());
    let operator_1 = harness.new_account_at(AccountAddress::from_hex_literal("0x21").unwrap());
    let operator_2 = harness.new_account_at(AccountAddress::from_hex_literal("0x22").unwrap());
    let amount = 25_000_000;
    let staker_address = *staker.address();
    let operator_1_address = *operator_1.address();
    let operator_2_address = *operator_2.address();
    assert_success!(harness.run_transaction_payload(
        &staker,
        aptos_stdlib::staking_contract_create_staking_contract(
            operator_1_address,
            operator_1_address,
            amount,
            10,
            vec![],
        )
    ));
    assert_success!(harness.run_transaction_payload(
        &staker,
        aptos_stdlib::staking_contract_add_stake(operator_1_address, amount)
    ));
```

**File:** aptos-move/e2e-move-tests/src/tests/stake.rs (L383-413)
```rust
    // Operator requests commissions.
    harness.new_block_with_metadata(pool_address, vec![]);
    harness.new_epoch();
    assert_success!(harness.run_transaction_payload(
        &staker,
        aptos_stdlib::staking_contract_request_commission(staker_address, operator_1_address)
    ));

    // Wait until stake is unlocked.
    harness.fast_forward(7200);
    harness.new_epoch();
    assert_success!(harness.run_transaction_payload(
        &staker,
        aptos_stdlib::staking_contract_distribute(staker_address, operator_1_address)
    ));

    // Staker unlocks some stake.
    harness.new_block_with_metadata(pool_address, vec![]);
    harness.new_epoch();
    assert_success!(harness.run_transaction_payload(
        &staker,
        aptos_stdlib::staking_contract_unlock_stake(operator_1_address, amount)
    ));

    // Wait until stake is unlocked.
    harness.fast_forward(7200);
    harness.new_epoch();
    assert_success!(harness.run_transaction_payload(
        &staker,
        aptos_stdlib::staking_contract_distribute(staker_address, operator_1_address)
    ));
```
