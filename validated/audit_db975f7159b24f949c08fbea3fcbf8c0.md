### Title
Operator-controlled `beneficiary_for_operator` with no coin-registration check permanently DoSes shared `distribution_pool`, freezing staker/delegator withdrawable funds - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move)

### Summary
`staking_contract::set_beneficiary_for_operator` lets an operator (an unprivileged, non-staker-owned role) redirect their commission payouts to any address, with no check that the address can actually receive `AptosCoin`. `distribute()` / `distribute_internal()` is explicitly permissionless ("Allow anyone to distribute already unlocked funds") and processes **all** shareholders of the single shared `distribution_pool` (staker's principal/rewards distributions *and* the operator's commission distribution) inside one atomic loop. If the operator's chosen beneficiary cannot accept a direct coin deposit, the whole transaction reverts every time `distribute()` (or any function that internally calls `distribute_internal`, e.g. `unlock_stake`, `request_commission`) is invoked, permanently trapping the staker's already-unlocked/withdrawable stake and rewards behind an operator-controlled poison address.

### Finding Description
`set_beneficiary_for_operator` sets the beneficiary with no validation: [1](#0-0) 

Compare this to `vesting::set_beneficiary`, which explicitly guards against this exact failure mode by requiring the new beneficiary to already be registered for APT before allowing the update: [2](#0-1) 

No equivalent `assert_account_is_registered_for_apt` (or similar) check exists in `staking_contract::set_beneficiary_for_operator`, nor in the analogous `delegation_pool::set_beneficiary_for_operator`: [3](#0-2) 

`distribute()` is intentionally permissionless and shared across all recipients that have unredeemed shares in the same `distribution_pool` (both the staker's distributions from `unlock_stake` and the operator's commission distribution from `request_commission_internal`, added via the shared `add_distribution` helper): [4](#0-3) [5](#0-4) 

The core failure point is inside `distribute_internal`, where the loop iterates over every shareholder of the pool in one atomic transaction and redirects the operator's payout to `beneficiary_for_operator(operator)`: [6](#0-5) 

`aptos_account::deposit_coins` will abort with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the target account exists, is not yet registered for `AptosCoin`, and has opted out of direct/unregistered coin transfers: [7](#0-6) 

Because Move transactions are atomic, if this abort happens on the operator's/beneficiary's turn in the `while` loop, **every** share redemption performed earlier in that same call (including the staker's own principal/reward distribution) is also rolled back. Since the underlying shares are never actually redeemed (the abort reverts the mutation), the pool state after the failed call is identical to before it — so every subsequent call to `distribute()`, `unlock_stake()`, or `request_commission()` for that staker/operator pair will deterministically hit the same abort forever, unless the operator later changes the beneficiary to a valid one (which the operator, not the staker, controls).

### Impact Explanation
This breaks the "unlock/withdraw paths must not strand funds permanently" invariant for `staking_contract` pools. An operator — a role that should only control the operator's own commission, not the staker's principal — can, by choosing a hostile/unregistered beneficiary, permanently freeze the shared `distribution_pool`, blocking the **staker's** already-unlocked stake and reward withdrawals indefinitely (until/unless the operator cooperates and fixes the beneficiary). This is a real "trap value" / "permanent lock of claim rights" impact on funds not owned by the attacker (the operator), affecting the staker who does not control the beneficiary setting. Because `staking_contract` pools back real, mainnet-relevant delegated stake, this can strand large staker balances with no recovery mechanism available to the staker.

### Likelihood Explanation
Likelihood is high and requires no special privilege beyond being a normal operator of a `staking_contract` (a role many external node operators legitimately hold). The attack is trivial to execute: create/control an address, disable acceptance of un-registered direct coin transfers for it (a standard, permissionless account setting) or simply never register it for `AptosCoin`, then call `staking_contract::set_beneficiary_for_operator` with that address. No governance, admin, or guardian approval is needed, matching the "reachable by unprivileged users" scope of this task.

### Recommendation
- Short term: In `staking_contract::set_beneficiary_for_operator` and `delegation_pool::set_beneficiary_for_operator`, require the new beneficiary to already be registered to receive `AptosCoin` (mirroring the check already present in `vesting::set_beneficiary`), or otherwise validate that `aptos_account::deposit_coins` will not abort for that address.
- Long term: Decouple the shared `distribution_pool`'s recipient processing so a single failing recipient (e.g., a broken/hostile beneficiary) cannot block distribution to other unrelated shareholders in the same transaction — e.g., skip/queue failed transfers instead of aborting the whole batch, or process distributions per-recipient in separate, independently retryable calls.

### Proof of Concept
1. Staker `S` creates a staking contract with operator `O` via `staking_contract::create_staking_contract` (commission > 0%).
2. `O` calls `staking_contract::set_beneficiary_for_operator(O, B)` where `B` is an address that either doesn't exist yet, or has called `aptos_account`'s direct-transfer opt-out and is not registered for `AptosCoin` (so `can_receive_direct_coin_transfers(B) == false` and `coin::is_account_registered<AptosCoin>(B) == false`).
3. Stake earns rewards; `S` calls `staking_contract::unlock_stake(S, O, amount)`. This internally calls `request_commission_internal`, which adds a distribution entry for `O` (to be redirected to `B`), and adds a distribution entry for `S` in the same `distribution_pool`.
4. Once the lockup passes and funds become inactive/withdrawable, anyone calls `staking_contract::distribute(S, O)`.
5. Inside `distribute_internal`'s `while` loop, when the iteration reaches `O`'s share entry, `recipient` is remapped to `B`, and `aptos_account::deposit_coins(B, ...)` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
6. The entire transaction reverts, including the share-redemption progress for `S`'s own distribution entry in the same pool.
7. Every subsequent call to `distribute`, `unlock_stake`, or `request_commission` for the `(S, O)` pair repeats this abort, permanently freezing `S`'s already-unlocked stake/rewards unless `O` chooses to fix `B`.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-838)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-853)
```text
    /// Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does
    /// not need to be restricted to just the staker or operator.
    public entry fun distribute(
        staker: address, operator: address
    ) acquires Store, BeneficiaryForOperator {
        assert_staking_contract_exists(staker, operator);
        let store = borrow_global_mut<Store>(staker);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        distribute_internal(
            staker,
            operator,
            staking_contract,
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L937-957)
```text
    /// Add a new distribution for `recipient` and `amount` to the staking contract's distributions list.
    fun add_distribution(
        operator: address,
        staking_contract: &mut StakingContract,
        recipient: address,
        coins_amount: u64,
    ) {
        let distribution_pool = &mut staking_contract.distribution_pool;
        let (_, _, _, total_distribution_amount) =
            stake::get_stake(staking_contract.pool_address);
        update_distribution_pool(
            distribution_pool,
            total_distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

        distribution_pool.buy_in(recipient, coins_amount);
        let pool_address = staking_contract.pool_address;
        emit(AddDistribution { operator, pool_address, amount: coins_amount });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-923)
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
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1272-1290)
```text
    public entry fun set_beneficiary_for_operator(
        operator: &signer,
        new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator = new_beneficiary;
        } else {
            move_to(operator, BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary });
        };

        emit(SetBeneficiaryForOperator {
            operator: operator_addr,
            old_beneficiary,
            new_beneficiary,
        });
```
