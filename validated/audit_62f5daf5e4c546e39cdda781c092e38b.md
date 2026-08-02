## Finding [1](#0-0) 
`staking_contract::set_beneficiary_for_operator` lets any operator set an arbitrary `new_beneficiary` address without any validation of that address's ability to receive funds, unlike the equivalent flow in `vesting.move`.

By contrast, `vesting::set_beneficiary` explicitly guards against this exact class of failure: [2](#0-1) 

### Title
Unvalidated operator beneficiary address permanently blocks staker fund withdrawal and operator-switching in `staking_contract.move` - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`set_beneficiary_for_operator` in `staking_contract.move` stores `new_beneficiary` without verifying the account can actually receive `AptosCoin`, unlike `vesting::set_beneficiary`, which explicitly calls `assert_account_is_registered_for_apt` "so distribute() wouldn't fail and block all other accounts from receiving APT." Because `distribute_internal` performs an unconditional `aptos_account::deposit_coins` to whatever `beneficiary_for_operator` resolves to, an operator can set a beneficiary that refuses direct coin transfers and thereby make every future `distribute()`, `unlock_stake()`, and `switch_operator()` call for that staking contract abort permanently.

### Finding Description
`distribute_internal` iterates over all shareholders of the `distribution_pool` (which contains both the staker's pending unlock distributions and the operator's commission) and unconditionally deposits funds to each recipient, substituting the beneficiary for the operator's own share: [3](#0-2) 

`aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient has not registered a `CoinStore<AptosCoin>` and has opted out of arbitrary coin transfers via `DirectTransferConfig`: [4](#0-3) 

`set_beneficiary_for_operator` never checks that `new_beneficiary` can accept such a deposit before persisting it: [5](#0-4) 

Because `distribute_internal` is invoked as a mandatory first step of `unlock_stake`, `switch_operator`, `switch_operator_with_same_commission`, and `distribute` itself, a single reverting deposit to a bad beneficiary aborts the entire transaction in all of these entry points: [6](#0-5) [7](#0-6) 

Notably, `switch_operator` — the staker's designated "escape hatch" from a misbehaving operator — also calls `distribute_internal` on the *old* operator's contract *before* performing the switch, so the staker cannot even leave the malicious operator once this state is triggered.

### Impact Explanation
This is a griefing/lockup vector on staker funds: an operator (an unprivileged, staker-chosen counterparty, not a protocol admin) can permanently trap the staker's principal, unlocked stake, and accrued rewards in the staking contract by pointing their beneficiary at an address that rejects direct coin transfers. This matches the "Permanent lock or non-recoverable loss of claim rights in stake ... commission, beneficiary ... flows" impact category, since the staker loses the ability to withdraw funds or switch away from the operator indefinitely, with no recovery path short of the operator voluntarily fixing their own beneficiary configuration.

### Likelihood Explanation
The precondition is straightforward and fully within the operator's control: create an account, call `aptos_account::set_allow_direct_coin_transfers(false)` on it, never register a `CoinStore<AptosCoin>`, then call `set_beneficiary_for_operator` with that address. No special privileges beyond being an operator of an existing staking contract are required, and the effect on the counterparty staker is immediate and persistent.

### Recommendation
Add the same guard used in `vesting::set_beneficiary` to `staking_contract::set_beneficiary_for_operator` (and, for defense-in-depth, to `delegation_pool::set_beneficiary_for_operator`): call `aptos_account::assert_account_is_registered_for_apt(new_beneficiary)` (or an equivalent check confirming the address can accept a direct `AptosCoin` deposit) before persisting the beneficiary change.

### Proof of Concept
1. Staker calls `staking_contract::create_staking_contract` with operator `O`, depositing stake.
2. `O` creates account `B`, calls `aptos_account::set_allow_direct_coin_transfers(B_signer, false)`, and never registers `CoinStore<AptosCoin>` for `B`.
3. `O` calls `staking_contract::set_beneficiary_for_operator(O, B)` — this succeeds because no validation exists.
4. Staker later calls `unlock_stake`, `distribute`, or `switch_operator` on this staking contract. `distribute_internal` reaches the shareholder loop, substitutes `B` for `O`'s commission share, and calls `aptos_account::deposit_coins(B, ...)`, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. Every subsequent call into `distribute_internal` for this staker/operator pair (via `distribute`, `unlock_stake`, `switch_operator`, `switch_operator_with_same_commission`) now reverts, permanently trapping the staker's funds with no way to exfiltrate them or switch operators.

Note: I was not able to fully trace whether the analogous `delegation_pool::set_beneficiary_for_operator` (which credits commission as pool shares rather than a direct coin push) has the same failure mode, since I could not confirm within available context whether `synchronize_delegation_pool` performs a direct `aptos_account::deposit_coins` to the beneficiary or only internal share accounting. If further investigation is needed there, a Devin session with full repository access would be required to trace `synchronize_delegation_pool`'s commission-crediting logic in `delegation_pool.move`.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L761-789)
```text
    /// Allows staker to switch operator without going through the lenghthy process to unstake.
    public entry fun switch_operator(
        staker: &signer,
        old_operator: address,
        new_operator: address,
        new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, old_operator);

        assert!(
            new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );
        // Merging two existing staking contracts is too complex as we'd need to merge two separate stake pools.
        let store = borrow_global_mut<Store>(staker_address);
        let staking_contracts = &mut store.staking_contracts;
        assert!(
            !staking_contracts.contains_key(&new_operator),
            error::invalid_state(ECANT_MERGE_STAKING_CONTRACTS)
        );

        let (_, staking_contract) = staking_contracts.remove(&old_operator);
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            old_operator,
            &mut staking_contract,
        );
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-924)
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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L121-131)
```text
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
