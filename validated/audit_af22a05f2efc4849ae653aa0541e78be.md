## Analysis

The ThorSwap bug is fundamentally about an **unvalidated destination address that receives funds atomically**, with no fallback if that address can't (or won't) actually receive the deposit — causing permanent loss/lockup of value that belongs to someone else.

The Aptos-native analog is in `aptos_framework::staking_contract`:

```move
// aptos-move/framework/aptos-framework/sources/staking_contract.move:810-838
public entry fun set_beneficiary_for_operator(
    operator: &signer, new_beneficiary: address
) acquires BeneficiaryForOperator {
    ...
    let operator_addr = signer::address_of(operator);
    let old_beneficiary = beneficiary_for_operator(operator_addr);
    if (exists<BeneficiaryForOperator>(operator_addr)) {
        borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator =
            new_beneficiary;
    } else { ... };
``` [1](#0-0) 

`new_beneficiary` is accepted **without any validation** that the address can actually receive `AptosCoin`. Compare this to `vesting::set_beneficiary`, which explicitly guards against exactly this class of bug:

```move
// aptos-move/framework/aptos-framework/sources/vesting.move:915-923
public entry fun set_beneficiary(...) {
    // Verify that the beneficiary account is set up to receive APT. This is a requirement so distribute() wouldn't
    // fail and block all other accounts from receiving APT if one beneficiary is not registered.
    assert_account_is_registered_for_apt(new_beneficiary);
``` [2](#0-1) 

This same check is **absent** from `staking_contract::set_beneficiary_for_operator` (and its `delegation_pool::set_beneficiary_for_operator` counterpart at [3](#0-2) ).

The payout path (`distribute_internal`) is a single atomic loop over all shareholders of the staking contract's distribution pool (the staker's unlocked-stake entries plus the operator's commission entry), sending each via `aptos_account::deposit_coins`:

```move
// staking_contract.move:888-911
while (distribution_pool.shareholders_count() > 0) {
    ...
    if (recipient == operator) {
        recipient = beneficiary_for_operator(operator);
    };
    aptos_account::deposit_coins(
        recipient, coin::extract(&mut coins, amount_to_distribute)
    );
    ...
};
``` [4](#0-3) 

`aptos_account::deposit_coins` aborts if the recipient is not registered for the coin and has opted out of direct coin transfers:

```move
// aptos_account.move:111-131
public fun deposit_coins<CoinType>(to: address, coins: Coin<CoinType>) acquires DirectTransferConfig {
    if (!account::exists_at(to)) { create_account(to); ... };
    if (!coin::is_account_registered<CoinType>(to)) {
        assert!(
            can_receive_direct_coin_transfers(to),
            error::permission_denied(EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS)
        );
        coin::register<CoinType>(&create_signer(to));
    };
    coin::deposit<CoinType>(to, coins)
}
``` [5](#0-4) 

Because Move aborts revert the entire transaction, an operator pointing their beneficiary at any account that has disabled direct coin transfers (`aptos_account::set_allow_direct_coin_transfers(false)`, a normal user-facing feature) makes the operator's own commission entry in the loop abort `distribute()` — and since the staker's unlocked-stake payout is processed in the **same atomic loop**, the staker (a different, unprivileged party who owns that stake) is unable to withdraw their own already-unlocked funds via `distribute`/`switch_operator`/`vesting::distribute` until the operator fixes the beneficiary.

### Title
Unvalidated `new_beneficiary` in `staking_contract::set_beneficiary_for_operator` lets an operator block staker withdrawals (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`set_beneficiary_for_operator` (staking_contract.move and its delegation_pool.move analog) accepts an arbitrary `new_beneficiary` address with no reachability/registration check, unlike `vesting::set_beneficiary` which explicitly validates this to prevent exactly this failure mode.

### Finding Description
An operator (unprivileged relative to the staker/other delegators) calls `set_beneficiary_for_operator` with a `new_beneficiary` that has disabled direct coin transfers via `aptos_account::set_allow_direct_coin_transfers(false)` and is not registered for `AptosCoin`. Any subsequent call to `distribute` (or `switch_operator`, which internally calls `distribute_internal`) fails inside the shared atomic distribution loop when it reaches the operator's commission entry, because `aptos_account::deposit_coins` aborts. Since the loop processes the staker's own unlocked-stake entries in the same transaction, the abort reverts the entire `distribute` call, permanently (until the operator voluntarily fixes the beneficiary) blocking the staker's ability to withdraw their own already-unlocked/inactive stake and rewards.

### Impact Explanation
This corrupts claim rights across an owner/operator boundary: an operator, without holding the staker role, can trap the staker's inactive/withdrawable stake indefinitely by pointing their beneficiary at a non-receiving address, satisfying the "permanent lock or non-recoverable loss of claim rights in stake ... commission ... beneficiary flows" and "wrong-role control ... without already holding that role" categories.

### Likelihood Explanation
Any operator can trigger this unilaterally with a single `set_beneficiary_for_operator` call and requires only that `operator_beneficiary_change_enabled()` feature flag be on (as it presumably would be for the feature to be usable). No special privileges beyond being an operator of a staking contract are needed, and disabling direct coin transfers on an account is a standard supported feature, not an edge case.

### Recommendation
Add `aptos_account::assert_account_is_registered_for_apt(new_beneficiary)` (as already done in `vesting::set_beneficiary`) to `staking_contract::set_beneficiary_for_operator` and `delegation_pool::set_beneficiary_for_operator`, and/or make `distribute_internal`'s per-recipient payout resilient to a single failing deposit (e.g., skip/hold funds for a non-receiving beneficiary instead of aborting the whole distribution).

### Proof of Concept
1. Staker creates a staking contract with `operator` via `staking_contract::create_staking_contract`.
2. `operator` calls `aptos_account::set_allow_direct_coin_transfers(false)` on a fresh, unregistered account `bad_beneficiary` (or simply uses any account that never registered for `AptosCoin` and disabled direct transfers).
3. `operator` calls `staking_contract::set_beneficiary_for_operator(operator, bad_beneficiary)`.
4. Staker unlocks stake via `staking_contract::unlock_stake`, waits for lockup, then calls `staking_contract::distribute(staker_address, operator_address)`.
5. The call reaches `aptos_account::deposit_coins(bad_beneficiary, commission_coins)` inside `distribute_internal`'s loop, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`, reverting the whole transaction and leaving the staker unable to withdraw their unlocked stake.

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
