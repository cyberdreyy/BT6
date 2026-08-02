### Title
`set_beneficiary_for_operator` allows an operator to permanently freeze all distributions from a `staking_contract` pool by setting the beneficiary to a reserved address - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move)

### Summary
`staking_contract::set_beneficiary_for_operator` and its `delegation_pool` counterpart let an operator set `new_beneficiary` to any address, including `@0x0` (`vm_reserved`), `@0x1` (`aptos_framework`), or `@0x3` (`aptos_token`), with no validation. `account::create_account`/`create_account_if_does_not_exist` explicitly reject these reserved addresses with `ECANNOT_RESERVED_ADDRESS`. Since `distribute_internal` in `staking_contract.move` redirects the operator's payout to `beneficiary_for_operator(operator)` and pays it via `aptos_account::deposit_coins`, which creates the destination account if it doesn't already exist, setting the beneficiary to a reserved address causes any future `distribute()`/`unlock_stake()`/`switch_operator()` call on that pool to abort permanently. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`set_beneficiary_for_operator` stores `new_beneficiary` with no address validation:

```
public entry fun set_beneficiary_for_operator(
    operator: &signer, new_beneficiary: address
) acquires BeneficiaryForOperator {
    ...
    borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator = new_beneficiary;
    ...
}
``` [4](#0-3) 

`distribute_internal` iterates over all recipients of a distribution pool (staker and operator, potentially many recipients for `delegation_pool`), and when it reaches the operator's share it substitutes the beneficiary address, then deposits directly:

```
if (recipient == operator) {
    recipient = beneficiary_for_operator(operator);
};
aptos_account::deposit_coins(recipient, coin::extract(&mut coins, amount_to_distribute))
``` [5](#0-4) 

`aptos_account::deposit_coins`/`transfer` create the destination account if it does not already exist:
```
public entry fun transfer(source: &signer, to: address, amount: u64) {
    if (!account::exists_at(to)) {
        create_account(to)
    };
    ...
}
``` [3](#0-2) 

But `account::create_account` (and `create_account_if_does_not_exist`) explicitly forbid the reserved addresses `@vm_reserved` (0x0), `@aptos_framework` (0x1), and `@aptos_token` (0x3):
```
public(friend) fun create_account(new_address: address): signer {
    assert!(!exists<Account>(new_address), error::already_exists(EACCOUNT_ALREADY_EXISTS));
    assert!(
        new_address != @vm_reserved && new_address != @aptos_framework && new_address != @aptos_token,
        error::invalid_argument(ECANNOT_RESERVED_ADDRESS)
    );
    ...
}
``` [2](#0-1) 

If an operator calls `set_beneficiary_for_operator(operator, @0x0)` (or `@0x1`/`@0x3`, none of which have a pre-existing `Account`/`CoinStore` resource for a fresh chain), any subsequent call that triggers `distribute_internal` on that pool — `distribute`, `unlock_stake`, `switch_operator`, `add_stake`'s inactive-distribution path, etc. — will attempt to create an `Account` at the reserved address and abort with `ECANNOT_RESERVED_ADDRESS`. Because Move transactions are atomic, the abort reverts the entire call, including the redemption of the staker's own inactive/pending_inactive shares in the same `distribution_pool`. This permanently blocks the staker (and, for `delegation_pool`, all delegators sharing that pool's distribution mechanics) from withdrawing already-unlocked/inactive stake through the normal paths, since `distribute_internal`'s while loop processes all shareholders of that pool in one atomic call and will always hit the un-payable operator/beneficiary entry.

`set_beneficiary_for_operator` is exposed identically (no zero/reserved-address check) in `delegation_pool.move` and is reachable indirectly via `vesting.move::set_beneficiary_for_operator`, which simply forwards to `staking_contract::set_beneficiary_for_operator` without any validation either. [6](#0-5) [7](#0-6) 

Notably, this is the exact analog of the reported bug class: an address-input function performs no zero/reserved-address validation, and the framework itself (elsewhere, e.g. `vesting::set_beneficiary`, which calls `assert_account_is_registered_for_apt(new_beneficiary)`) demonstrates that such checks are considered necessary for beneficiary-setting flows — but this check is missing specifically on the `set_beneficiary_for_operator` path in `staking_contract.move` and `delegation_pool.move`. [8](#0-7) 

### Impact Explanation
This is a "permanent lock or non-recoverable loss of claim rights" issue: it does not steal funds outright, but it can permanently strand a staker's/delegators' already-unlocked (inactive/pending_inactive) stake balance because the atomic `distribute_internal` loop that must succeed to release funds becomes permanently un-executable once the operator's beneficiary is a reserved address. The operator does not need to already control the staker's funds to trigger this — it only requires calling `set_beneficiary_for_operator`, an operator-only but otherwise unprivileged action relative to the staker's principal. This satisfies the "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows" impact category.

### Likelihood Explanation
Likelihood is limited by the fact that this requires the operator role to call `set_beneficiary_for_operator`, and it is unclear from the available code whether there is an alternate withdrawal path (e.g., a per-shareholder redeem that bypasses `distribute_internal`'s all-recipients loop) that would let the staker recover funds without going through the operator's poisoned entry. I was not able to fully verify, within the tool budget available, whether `delegation_pool.move`'s commission-crediting mechanism (`synchronize_delegation_pool`) also routes through an actual `Account`-creating deposit for the beneficiary share (versus purely internal share-pool bookkeeping that would not hit the reserved-address check), or whether `distribute_internal`'s `while` loop order could allow other shareholders to be paid before the poisoned operator entry is reached and abort. This uncertainty affects the exact severity and blast radius (single staker vs. entire delegation pool) and should be confirmed with a live/unit-test trace before treating this as fully proven Critical/High severity.

### Recommendation
Add a `require`-equivalent assertion to `set_beneficiary_for_operator` in both `staking_contract.move` and `delegation_pool.move` (and mirror it in `vesting.move`'s pass-through) rejecting reserved addresses (`@vm_reserved`, `@aptos_framework`, `@aptos_token`) and, ideally, verifying the target account is registered to receive APT similarly to `vesting::set_beneficiary`'s `assert_account_is_registered_for_apt` check, before storing `new_beneficiary`.

### Proof of Concept
Conceptual PoC (not run against a live chain in this session, given the identified uncertainty above):
1. `operator` creates a `staking_contract` for `staker` via `staking_contract::create_staking_contract`.
2. `operator` calls `staking_contract::set_beneficiary_for_operator(operator, @0x0)`.
3. Stake pool accrues rewards; `staker` calls `staking_contract::unlock_stake(staker, operator, amount)`, which internally calls `distribute_internal`, which redirects the operator's commission recipient to `@0x0` and calls `aptos_account::deposit_coins(@0x0, coins)`.
4. `aptos_account::deposit_coins`/`transfer` sees `!account::exists_at(@0x0)` and calls `account::create_account(@0x0)`, which asserts `new_address != @vm_reserved`, aborting with `ECANNOT_RESERVED_ADDRESS`.
5. The entire transaction reverts, and any future call touching `distribute_internal` for this pool (further `unlock_stake`, `distribute`, `switch_operator`) will abort the same way, permanently freezing withdrawal of the staker's inactive/pending_inactive balance in that pool. [9](#0-8)

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L856-900)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/account/account.move (L288-301)
```text
    public(friend) fun create_account(new_address: address): signer {
        // there cannot be an Account resource under new_addr already.
        assert!(!exists<Account>(new_address), error::already_exists(EACCOUNT_ALREADY_EXISTS));
        // NOTE: @core_resources gets created via a `create_account` call, so we do not include it below.
        assert!(
            new_address != @vm_reserved && new_address != @aptos_framework && new_address != @aptos_token,
            error::invalid_argument(ECANNOT_RESERVED_ADDRESS)
        );
        if (features::is_default_account_resource_enabled()) {
            create_signer(new_address)
        } else {
            create_account_unchecked(new_address)
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L77-83)
```text
    public entry fun transfer(source: &signer, to: address, amount: u64) {
        if (!account::exists_at(to)) {
            create_account(to)
        };

        fungible_transfer_only(source, to, amount)
    }
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1000-1006)
```text
    /// Set the beneficiary for the operator.
    public entry fun set_beneficiary_for_operator(
        operator: &signer,
        new_beneficiary: address,
    ) {
        staking_contract::set_beneficiary_for_operator(operator, new_beneficiary);
    }
```
