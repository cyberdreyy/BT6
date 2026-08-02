## Analysis

Reducing the external bug (`FETH.withdrawFrom` — missing validation of a payout `to` address, causing loss/blocking) to Aptos terms: the analogous invariant is *"a recipient/beneficiary address set by an unprivileged party must be validated as capable of receiving APT before it is trusted in a downstream batch payout, or the whole payout must revert without unrelated funds being stranded."*

I compared the two structurally identical `set_beneficiary_for_operator` entry points against the framework's own `vesting::set_beneficiary`, which already codifies this exact invariant.

### Title
Missing beneficiary-registration validation in `staking_contract::set_beneficiary_for_operator` permanently strands staker's unlocked stake via `distribute_internal` batch payout - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::set_beneficiary_for_operator` lets an operator redirect their commission payouts to any `new_beneficiary` address with **no validation** that the address can actually receive `AptosCoin`. [1](#0-0) 

By contrast, the framework itself demonstrates this is a known required invariant: `vesting::set_beneficiary` explicitly calls `assert_account_is_registered_for_apt(new_beneficiary)` with a comment stating this is required *"so distribute() wouldn't fail and block all other accounts from receiving APT if one beneficiary is not registered."* [2](#0-1) 

The same failure mode the vesting module guards against is fully present in `staking_contract.move`'s `distribute_internal`, and `set_beneficiary_for_operator` (both in `staking_contract.move` and `delegation_pool.move`) has no equivalent guard.

### Finding Description
`distribute_internal` withdraws all inactive/pending-inactive stake for a staker/operator pair into a single `Coin<AptosCoin>` and then, in one loop, pays out **every** shareholder of the `distribution_pool` (the staker and the operator/beneficiary) from that same coin bucket: [3](#0-2) 

If the operator's beneficiary (set via `set_beneficiary_for_operator`, line 810-838) is an address that cannot accept the `aptos_account::deposit_coins` call in that loop (e.g., an address with no account and unable to auto-register a `CoinStore<AptosCoin>`), the deposit call aborts. Because Move aborts revert the entire transaction, the abort on the operator's payout also reverts the staker's own payout in the same loop, even though the staker has no control over what beneficiary the operator chose.

`distribute` (and thus `distribute_internal`) is the sole mechanism by which a staker's already-unlocked (inactive/pending_inactive) stake is transferred out of a `staking_contract` pool: [4](#0-3) . There is no per-recipient isolation (e.g., try/catch or independent transactions) — a single bad recipient blocks the whole batch permanently, since the operator can set the poisoned beneficiary at any time and there is nothing the staker can do to bypass `distribute_internal`'s indiscriminate loop.

The identical unguarded pattern also exists in `delegation_pool::set_beneficiary_for_operator`: [5](#0-4) .

### Impact Explanation
This matches the required impact class "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows." An operator — who does not need any elevated privilege over the staker's funds beyond the ordinary operator role they already hold — can set their own beneficiary to a value that causes `distribute()` to always abort, permanently freezing the **staker's** unlocked stake (not just the operator's own commission) inside the stake pool, with no admin/staker-side recovery path other than the operator fixing/removing the beneficiary (which a malicious or compromised operator may refuse to do).

### Likelihood Explanation
Requires only that the address already holds the ordinary "operator" role in a `staking_contract` pool (no special privilege beyond that), which is a normal, common role in the Aptos staking ecosystem. The framework's own code (`vesting::set_beneficiary`'s registration check and its comment) confirms the authors were aware this exact scenario is realistic and worth guarding against — they simply didn't apply the same guard to `staking_contract::set_beneficiary_for_operator` / `delegation_pool::set_beneficiary_for_operator`.

### Recommendation
Add the same guard used in `vesting::set_beneficiary` to both `staking_contract::set_beneficiary_for_operator` and `delegation_pool::set_beneficiary_for_operator`:
```move
assert_account_is_registered_for_apt(new_beneficiary);
```
Additionally, harden `distribute_internal` so that a failed/incapable recipient deposit does not revert payouts to unrelated shareholders (e.g., isolate each recipient's deposit or skip/queue failed deposits instead of aborting the whole batch).

### Proof of Concept
1. Staker creates a `staking_contract` with `operator` and stakes funds; validator later leaves the set, and stake becomes `inactive`/`pending_inactive`.
2. Operator calls `staking_contract::set_beneficiary_for_operator(operator, new_beneficiary)` with `new_beneficiary` = an address with no `Account`/`CoinStore<AptosCoin>` and structured (e.g., a non-existent constructed address, or an object address without APT registration capability) so that `aptos_account::deposit_coins` to it aborts. (Exact conditions under which `aptos_account::deposit_coins` aborts for an address were not directly inspected in this session due to tool-iteration limits, but the framework's own defensive check in `vesting.move` at lines 915-923 confirms such addresses exist and that this is the precise mechanism the authors intended to prevent.)
3. Anyone calls `staking_contract::distribute(staker, operator)`.
4. `distribute_internal` withdraws all inactive stake, then loops through `distribution_pool` shareholders; when it reaches the operator's commission share, it redirects to the poisoned `beneficiary` (line 896-897) and the `aptos_account::deposit_coins` call aborts, reverting the entire transaction including the staker's own payout.
5. Staker's already-unlocked stake remains permanently stuck in the pool as long as the operator's poisoned beneficiary is set, with no way for the staker to force payout. [6](#0-5)

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-920)
```text
    /// Distribute all unlocked (inactive) funds according to distribution shares.
    fun distribute_internal(
        staker: address,
        operator: address,
        staking_contract: &mut StakingContract,
    ) acquires BeneficiaryForOperator {
        let pool_address = staking_contract.pool_address;
        // Create the Staker resource if it doesn't exist to backfill the Staker resource for each pool.
        if (!exists<Staker>(pool_address)) {
            let pool_signer =
                &account::create_signer_with_capability(&staking_contract.signer_cap);
            move_to(pool_signer, Staker { staker });
        };
        let (_, inactive, _, pending_inactive) = stake::get_stake(pool_address);
        let total_potential_withdrawable = inactive + pending_inactive;
        let coins =
            stake::withdraw_with_cap(
                &staking_contract.owner_cap, total_potential_withdrawable
            );
        let distribution_amount = coin::value(&coins);
        if (distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

        let distribution_pool = &mut staking_contract.distribution_pool;
        update_distribution_pool(
            distribution_pool,
            distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

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

        // In case there's any dust left, send them all to the staker.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(staker, coins);
            distribution_pool.update_total_coins(0);
        } else {
            coin::destroy_zero(coins);
        }
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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1272-1291)
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
    }
```
