## Analysis: Missing recipient-registration check in `staking_contract::set_beneficiary_for_operator` permanently locks staker's stake distribution

### Title
Operator-controlled beneficiary address without APT-registration check permanently blocks `distribute_internal`, freezing staker stake and rewards - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::set_beneficiary_for_operator` lets any operator redirect their commission payouts to an arbitrary `new_beneficiary` address with no check that the address can actually receive APT. [1](#0-0) 
That beneficiary is later paid inside `distribute_internal`, which loops over *all* shareholders of the pool (staker + operator/beneficiary) and calls `aptos_account::deposit_coins` for each one in a single atomic transaction. [2](#0-1) 
If the deposit to the beneficiary aborts (e.g., the beneficiary account is unregistered for APT and has opted out of direct coin transfers via `aptos_account::set_allow_direct_coin_transfers(..., false)`), the entire `distribute_internal` call reverts — which also reverts the staker's own withdrawal, since `unlock_stake`, `unlock_rewards`, `switch_operator`, and `distribute` all call `distribute_internal` before doing anything else. [3](#0-2) [4](#0-3) 

### Finding Description
This is the exact bug-class from the external report: code assumes a recipient (the "owner"/beneficiary) can always receive a value transfer, and does not verify that assumption before making other, unrelated operations depend on it.

The sibling module `vesting.move` shows the framework authors were aware of exactly this risk and fixed it there: `vesting::set_beneficiary` explicitly calls `assert_account_is_registered_for_apt(new_beneficiary)` with a comment stating this is required so that `distribute()` "wouldn't fail and block all other accounts from receiving APT if one beneficiary is not registered." [5](#0-4) 

`staking_contract::set_beneficiary_for_operator`, however, has no equivalent check — it unconditionally stores `new_beneficiary` under the operator's `BeneficiaryForOperator` resource: [6](#0-5) 

`aptos_account::deposit_coins`/`transfer_coins` will abort with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the target is not registered for the coin and has explicitly disabled `can_receive_direct_coin_transfers`: [7](#0-6) 

Because `distribute_internal` iterates and pays out *every* shareholder — including redirecting the operator's share to `beneficiary_for_operator(operator)` — a single non-cooperative beneficiary poisons the whole distribution for that `StakingContract`, which is keyed by `(staker, operator)`: [8](#0-7) 

### Impact Explanation
Once the operator sets a beneficiary that reverts on deposit, every entry point that reaches `distribute_internal` for that pool becomes permanently unusable:
- `unlock_stake` / `unlock_rewards` (staker trying to withdraw their own unlocked principal or rewards)
- `switch_operator` / `switch_operator_with_same_commission`
- `distribute` (public, callable by anyone but always reverts)

This traps the staker's already-inactive/pending-inactive stake and unpaid commission with no recovery path short of governance intervention, matching the "permanent lock or non-recoverable loss of claim rights in stake ... beneficiary ... flows" impact category. The staker never consented to and cannot control the operator's beneficiary choice, so an uncooperative or careless operator can grief a staker's funds indefinitely.

### Likelihood Explanation
`set_beneficiary_for_operator` is callable by any operator signer (gated only by a feature flag, not by staker permission), and `aptos_account::set_allow_direct_coin_transfers(false)` is a normal, permissionless account setting. No special privilege is required to trigger the condition — an operator only needs to point the beneficiary at an address (their own alt account, or any account) that has opted out of unregistered-coin deposits, or is simply never registered and opts out by default configuration change. This is a straightforward, unprivileged, reliably reproducible action.

### Recommendation
Mirror the fix already present in `vesting.move`: require `aptos_account::assert_account_is_registered_for_apt(new_beneficiary)` (or an equivalent capability check) inside `staking_contract::set_beneficiary_for_operator` before persisting the new beneficiary. Additionally, consider making `distribute_internal` resilient to a single failing recipient (e.g., skip/queue a failed payout rather than aborting the whole distribution) so that one poisoned recipient cannot block payouts to the rest of the pool's shareholders.

### Proof of Concept
1. Staker creates a staking contract with `operator` via `staking_contract::create_staking_contract`.
2. `operator` (or an account they control) calls `aptos_account::set_allow_direct_coin_transfers(beneficiary_signer, false)` on a fresh, unregistered `beneficiary` account.
3. `operator` calls `staking_contract::set_beneficiary_for_operator(operator, beneficiary_address)` — succeeds with no registration check.
4. Stake pool earns rewards / lockup expires, creating unlocked funds due to staker.
5. Staker calls `unlock_stake` or anyone calls `distribute(staker_address, operator_address)`; `distribute_internal` attempts `aptos_account::deposit_coins(beneficiary_address, ...)` for the operator's commission share, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
6. The entire transaction reverts, so the staker cannot withdraw their own already-unlocked principal/rewards either, and this holds true for every future call into this pool until the beneficiary is fixed — something only the operator, not the staker, controls.

Note: I was not able to view the full body of `aptos_account::deposit_coins` (lines 60-120 of `aptos_account.move` were truncated in the index); the cited abort condition is inferred from the visible `transfer_coins`/`batch_transfer_coins` logic at lines 121-130, which follow the same `is_account_registered` / `can_receive_direct_coin_transfers` pattern. If the exact `deposit_coins` implementation differs, verifying the full source directly (e.g., via a Devin session) is recommended to confirm the abort path precisely.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L691-696)
```text
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L785-789)
```text
        distribute_internal(
            staker_address,
            old_operator,
            &mut staking_contract,
        );
```

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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L121-130)
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
```
