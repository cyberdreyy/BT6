## Title
Malicious operator can permanently DoS `distribute()` for all shareholders by setting a beneficiary/address that rejects direct coin transfers - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`distribute_internal` in `staking_contract.move` iterates over **every** shareholder in a single `StakingContract`'s `distribution_pool` and pays them out in one atomic loop via `aptos_account::deposit_coins`. Because `deposit_coins` aborts if the destination has explicitly disabled direct coin transfers and is not yet registered for `AptosCoin`, any single shareholder that cannot receive the payout causes the **entire** `distribute()`/`unlock_stake()`/`request_commission()`/`switch_operator()` call to abort — permanently blocking withdrawal for every other legitimate party sharing that pool (the staker, and any stale prior operator whose commission is still pending in the same pool, per `switch_operator`'s test which shows unpaid commission for an old operator persisting in the new `StakingContract`'s `distribution_pool`).

### Finding Description
`distribute_internal` (`staking_contract.move:856-920`) loops:
```
while (distribution_pool.shareholders_count() > 0) {
    ...
    aptos_account::deposit_coins(recipient, coin::extract(&mut coins, amount_to_distribute));
    ...
}
``` [1](#0-0) 

`aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient's `CoinStore<AptosCoin>` is unregistered and the account has disabled direct transfers via `set_allow_direct_coin_transfers(false)`: [2](#0-1) 

The commission recipient is resolved via `beneficiary_for_operator(operator)`, and `set_beneficiary_for_operator` lets the operator set **any** address as beneficiary, with no validation that the address can actually receive `AptosCoin`: [3](#0-2) 

`distribute_internal` is invoked from every important user-facing entry point of this module — not just `distribute()`, but also `unlock_stake` and `request_commission`, which force a distribution first: [4](#0-3) [5](#0-4) 

Additionally, `switch_operator` does not create a fresh `distribution_pool` for the new operator; unpaid commission owed to the *old* operator remains tracked as a shareholder inside the same pool that the new operator/staker will later share, as confirmed by the test asserting a pending distribution for `operator_1_address` inside `operator_2_address`'s staking contract: [6](#0-5) 

This means a malicious (or since-departed) operator's poisoned recipient address stays a landmine in the shared pool indefinitely, blocking the staker's (and the new operator's) legitimate withdrawal even after the malicious operator is no longer involved.

### Impact Explanation
Once an operator (or their beneficiary) sets a receiving address that rejects direct `AptosCoin` transfers (an address that exists, is not yet registered for `AptosCoin`, and has called `set_allow_direct_coin_transfers(false)`), every subsequent call to `distribute`, `unlock_stake`, `request_commission`, or `update_commission_percentage` for that staker/operator pair will abort deterministically inside `distribute_internal`'s payout loop. This permanently traps the staker's already-unlocked/inactive stake and any pending commission in the stake pool with no on-chain recovery path, satisfying "permanent lock or non-recoverable loss of claim rights in stake ... commission ... flows" and "operator commission ... payout ... corruption that ... traps value."

### Likelihood Explanation
The precondition is fully attacker-controlled and requires no privileged role beyond being (or becoming) the operator of a staking contract, which is an ordinary, permissionless role a staker can assign. Setting `allow_arbitrary_coin_transfers = false` on an unregistered-for-`AptosCoin` account and pointing `set_beneficiary_for_operator` at it are both standard, permissionless entry-point calls. No governance or admin assumption is required, and no race condition is needed since the block is deterministic and permanent once set.

### Recommendation
Do not let a single payee's inability to receive funds block the entire `distribution_pool` payout loop. Options:
- Wrap each `aptos_account::deposit_coins` call so that failures for one recipient do not abort the whole transaction (e.g., detect via `coin::is_account_registered` / `aptos_account::can_receive_direct_coin_transfers` beforehand and re-queue/skip un-payable shares instead of aborting).
- Alternatively, switch to a pull-based withdrawal model per shareholder (similar to `delegation_pool`'s per-user `withdraw`) instead of the current shared, atomic all-or-nothing loop.
- Validate that `new_beneficiary` in `set_beneficiary_for_operator` is capable of receiving `AptosCoin` before accepting it, and ensure `switch_operator` settles/flushes any prior operator's pending distribution instead of carrying it forward into a new shared pool.

### Proof of Concept
1. Staker creates a staking contract with `operator` via `create_staking_contract` (commission > 0).
2. `operator` creates (or controls) an auxiliary account `evil`, ensures it is **not** registered for `AptosCoin`, and calls `aptos_account::set_allow_direct_coin_transfers(evil_signer, false)`.
3. `operator` calls `staking_contract::set_beneficiary_for_operator(operator, evil_address)`. [7](#0-6) 
4. Stake pool earns rewards; anyone (or the staker) calls `unlock_stake` (or `distribute`), which invokes `distribute_internal`, which reaches `evil_address` in the payout loop and calls `aptos_account::deposit_coins(evil_address, ...)`.
5. `deposit_coins` aborts (`EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`) because `evil` is unregistered for `AptosCoin` and has disabled direct transfers, reverting the entire transaction. [2](#0-1) 
6. All future `distribute`, `unlock_stake`, and `request_commission` calls on this staker/operator pair now permanently abort, locking the staker's unlocked/inactive stake and any commission in the stake pool.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L618-629)
```text

        let store = borrow_global_mut<Store>(staker);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        // Short-circuit if zero commission.
        if (staking_contract.commission_percentage == 0) { return };

        // Force distribution of any already inactive stake.
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-695)
```text
    /// Staker can call this to request withdrawal of part or all of their staking_contract.
    /// This also triggers paying commission to the operator for accounting simplicity.
    public entry fun unlock_stake(
        staker: &signer, operator: address, amount: u64
    ) acquires Store, BeneficiaryForOperator {
        // Short-circuit if amount is 0.
        if (amount == 0) return;

        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, operator);

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);

        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-838)
```text




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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1568-1577)
```text
        // The staking_contract is now associated with operator 2 but there should be a pending distribution of unpaid
        // commission to operator 1.
        let new_balance = with_rewards(INITIAL_BALANCE);
        let commission_for_operator_1 = (new_balance - INITIAL_BALANCE) / 10;
        assert_distribution(
            staker_address,
            operator_2_address,
            operator_1_address,
            commission_for_operator_1
        );
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
