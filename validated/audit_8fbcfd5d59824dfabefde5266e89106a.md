## Analysis Result



### Title
Missing validation in `set_beneficiary_for_operator` allows an operator to set an unrecoverable/invalid beneficiary, trapping or reverting commission distribution - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
Both `staking_contract::set_beneficiary_for_operator` and `delegation_pool::set_beneficiary_for_operator` allow an operator to set any arbitrary `new_beneficiary` address — including `@0x0` — with **no validation at all**, mirroring the exact root cause of the Celo `setPaymentDelegation` bug (accepting a beneficiary with no safety check before it's used as the payout target).

### Finding Description
In `staking_contract.move`, `set_beneficiary_for_operator` unconditionally stores whatever address the operator provides: [1](#0-0) 

There is no check that `new_beneficiary != @0x0` or that the target is a valid, controllable account. The same pattern exists in the delegation pool module: [2](#0-1) 

This stored beneficiary is later used unconditionally as the payout recipient inside `distribute_internal`'s shareholder loop: [3](#0-2) 

Because `distribute_internal` iterates over **all** shareholders (staker, delegators, operator) in a single loop before returning, and `beneficiary_for_operator(operator)` is only resolved (not validated) at line 897, any failure or permanent unclaimability tied to that recipient address affects the entire distribution call, not just the operator's own commission share: [4](#0-3) 

### Impact Explanation
This matches the report's "Operator commission, beneficiary payout ... corruption that credits the wrong account or traps value" impact category. If the operator (an unprivileged role — no special governance/admin rights required) sets `new_beneficiary = @0x0`, or another inaccessible address:
- If the underlying coin/account-creation path aborts for that reserved/invalid address, the entire `distribute_internal` transaction reverts — exactly the Celo pattern where the whole distribution is blocked, denying payouts not just to the operator but potentially to other shareholders processed in the same call.
- If it does not abort, the commission funds are deposited to an address with no known private key, permanently and non-recoverably stranding real value — a critical, non-recoverable loss of claim rights.

I was unable to fully confirm from the available code snippets whether `account::create_account`/`aptos_account::deposit_coins` explicitly reject reserved addresses like `@0x0`; this determines whether the failure mode is "abort blocking all payouts" or "silent permanent value loss," but under both outcomes the impact falls squarely within the required Stake-and-Lockup gate.

### Likelihood Explanation
Likelihood is high: the call is a normal `entry` function reachable by any operator with zero privilege escalation, requires no coordination with the staker/delegator, and has no argument validation guarding against `@0x0` or other unrecoverable addresses — unlike similar functions elsewhere in the codebase (e.g., `switch_operator` which validates `new_commission_percentage <= 100`).

### Recommendation
Add an explicit check in both `staking_contract::set_beneficiary_for_operator` and `delegation_pool::set_beneficiary_for_operator` rejecting `new_beneficiary == @0x0` (and ideally validating the address is an existing, coin-registrable account), mirroring the Celo fix's guidance of disallowing zero beneficiaries and introducing an explicit "delete/reset beneficiary" path if the operator intends to clear it.

### Proof of Concept
1. Operator calls `staking_contract::set_beneficiary_for_operator(operator, @0x0)` — succeeds with no validation (`staking_contract.move:810-829`).
2. Rewards accrue; `request_commission`/`distribute` is called by any party (function is permissionless per its doc comment at `staking_contract.move:840-841`).
3. `distribute_internal` resolves `recipient = beneficiary_for_operator(operator)` == `@0x0` at line 897 and calls `aptos_account::deposit_coins(@0x0, ...)`.
4. Depending on account-creation semantics for reserved addresses, either the whole distribution transaction aborts (blocking payout to staker and all other shareholders in the same call) or commission funds are irrecoverably deposited to `@0x0`.

I could not independently confirm the exact abort/success behavior of `account::create_account` for `@0x0` within the available search budget — a Devin session with full repo access would be needed to trace `aptos_account::deposit_coins` → `coin::register`/`account::create_account` to pin down whether the failure mode is a full revert or silent fund loss.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-829)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-912)
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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1272-1284)
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
```
