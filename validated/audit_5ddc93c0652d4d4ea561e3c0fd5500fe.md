## Analysis

The reported bug class ("public/no-privilege-check functions whose unconstrained inputs can corrupt shared accounting or lock others' funds") maps in Aptos's stake/lockup modules to `staking_contract::distribute()` combined with the unrestricted `staking_contract::set_beneficiary_for_operator()`.

`distribute()` in `staking_contract.move` is a permissionless entry function that pays out **every** pending recipient of a staking contract's shared `distribution_pool` — both the staker's withdrawn principal/rewards and the operator's (or its beneficiary's) commission — inside a single loop and a single atomic transaction: [1](#0-0) 

Each payout uses `aptos_account::deposit_coins`, which **reverts the whole transaction** if the recipient address has never registered a `CoinStore<AptosCoin>` and has opted out of arbitrary direct transfers: [2](#0-1) 

The operator controls the beneficiary recipient via `set_beneficiary_for_operator`. In `delegation_pool.move`, the analogous, confirmed-full function shows this is settable by the operator with **no validation whatsoever** of the new address (no existence check, no registration check): [3](#0-2) 

`staking_contract::set_beneficiary_for_operator` (declared at) has an identical doc comment/signature pattern and, unlike `vesting::set_beneficiary`, is not shown importing/calling a registration assertion: [4](#0-3) 

By contrast, the framework authors clearly recognized this exact risk in the vesting module, where `set_beneficiary` explicitly guards against it: [5](#0-4) 

I could not retrieve the exact body of `staking_contract::set_beneficiary_for_operator` (lines ~811–830) due to a tool read gap, so I cannot cite its internals directly — this is inferred from the doc comment, the shared naming/pattern with `delegation_pool`'s fully-confirmed version, and the absence of a `vesting`-style registration check anywhere else in the file's visible imports/usages.

### Title
Operator can permanently freeze a staker's unlocked stake in `staking_contract` by pointing their beneficiary at an unregistered, transfer-opted-out address - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::distribute()` pays every shareholder of a staking contract's shared `distribution_pool` — the staker and the operator's beneficiary — in one atomic loop using `aptos_account::deposit_coins`. That helper aborts the entire transaction if a recipient has never registered a `CoinStore<AptosCoin>` and has disabled arbitrary direct transfers via `aptos_account::set_allow_direct_coin_transfers(false)`. Because `set_beneficiary_for_operator` lets the operator set this address unilaterally with no existence/registration check, a malicious or compromised operator can poison the shared distribution recipient list, permanently blocking `distribute()` (and any path that internally forces distribution, e.g. `unlock_stake`, `request_commission`, `switch_operator`) — trapping the staker's own already-unlocked principal and rewards.

### Finding Description
1. Operator calls `staking_contract::set_beneficiary_for_operator(operator, poison_addr)` where `poison_addr` is an address they control that has never registered `CoinStore<AptosCoin>` and has called `aptos_account::set_allow_direct_coin_transfers(false)`.
2. Any subsequent call to `distribute()` (by the staker or anyone, since it's permissionless) reaches the operator's commission payout in the shared loop and calls `aptos_account::deposit_coins(poison_addr, ...)`.
3. `deposit_coins` sees `poison_addr` is unregistered and disallows arbitrary transfers, so it aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
4. Because Move transactions are atomic, the abort reverts the staker's own portion of the distribution too — even though the staker's payout in isolation would have succeeded.
5. Since the staker cannot bypass `distribute()` (it's the only path to move funds out of the pending distribution pool once withdrawn from the stake pool), the staker's principal/rewards become permanently unrecoverable as long as the poisoned beneficiary remains set and unregistered.

### Impact Explanation
This breaks the "unlock, reactivate, withdraw ... must not strand [value] permanently" invariant and the "Owner ... boundaries must hold without assuming attacker already has role" requirement: the operator role (not privileged over the staker's principal) can trap the staker's funds indefinitely with a single ungated call. This is a high-severity, non-recoverable loss of claim rights over stake balances that are otherwise legitimately owned by the staker.

### Likelihood Explanation
Low complexity, fully unprivileged from the staker's perspective (the operator is the only party needed, and operators are semi-trusted but not meant to have unilateral power over the staker's principal). No special conditions beyond the operator calling two ordinary, publicly available entry functions (`set_allow_direct_coin_transfers` on a controlled account, then `set_beneficiary_for_operator`).

### Recommendation
- Add `aptos_account::assert_account_is_registered_for_apt(new_beneficiary)` (as already done in `vesting::set_beneficiary`) to `staking_contract::set_beneficiary_for_operator` and `delegation_pool::set_beneficiary_for_operator`.
- More robustly, decouple the staker's payout from the operator/beneficiary's payout in `distribute_internal`, e.g. by using `try`-style per-recipient deposits (skip/queue a failing recipient instead of aborting the whole loop), so one poisoned recipient cannot block payouts to everyone else in the same distribution pool.

### Proof of Concept
Local code review only (per report constraints, no deployed harness executed):
1. Operator account `O` calls `aptos_account::set_allow_direct_coin_transfers(false)` on itself or on a fresh throwaway address `B` it controls, never calling `coin::register<AptosCoin>(B)`.
2. Operator calls `staking_contract::set_beneficiary_for_operator(operator=O, new_beneficiary=B)` — succeeds, no registration check as shown in the fully-confirmed sibling implementation [3](#0-2) .
3. Staker calls `staking_contract::unlock_stake(staker, O, amount)`, which internally calls `distribute_internal` then `request_commission_internal` [6](#0-5) ; once the lockup period elapses and `distribute()` is invoked to actually pay out, the loop at [1](#0-0)  reaches `B` and aborts via `aptos_account::deposit_coins` [2](#0-1) , reverting the whole transaction and leaving the staker's funds stuck in the distribution pool indefinitely.

**Uncertainty flagged:** the exact body of `staking_contract::set_beneficiary_for_operator` (lines ~811–830) was not retrievable due to a tool/indexing gap in this session — I recommend a Devin session with full file access to directly confirm the function body and validate the fix.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L691-703)
```text
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        let commission_paid =
            request_commission_internal(
                operator,
                staking_contract,
            );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-810)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
    public entry fun set_beneficiary_for_operator(
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
