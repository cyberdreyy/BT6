No vulnerability found for this question.

**Analysis:**

`vesting::distribute` is intentionally a permissionless `public entry fun` — the code comment on the analogous `staking_contract::distribute` function explicitly states callers "do not need to be restricted to just the staker or operator" [1](#0-0) , and `vesting::distribute` follows the same pattern, allowing anyone to trigger distribution of unlocked funds [2](#0-1) .

The premised attack — that `distribute` could use a beneficiary lookup reflecting an "in-flight, attacker-uninitiated beneficiary change" — doesn't hold up under Move's execution model. `set_beneficiary` is a single atomic transaction that updates the `beneficiaries` map under `verify_admin` authorization before returning [3](#0-2) . There is no partial/intermediate state observable across transactions — each Move transaction executes to completion before the next one is processed, so `distribute` called "right after" `set_beneficiary` will simply see the fully-committed new beneficiary value via `get_beneficiary(vesting_contract, shareholder)` [4](#0-3) . That is the intended, correct behavior — funds go to whichever beneficiary is currently configured, and only the admin (via `verify_admin`) can change that mapping.

Since `distribute` never lets the unprivileged caller choose or influence the beneficiary/shareholder addresses — it only reads state that was legitimately set by the admin — there is no redirection of funds to an attacker-controlled or incorrect account. The permissionless nature of `distribute` is by design (so distributions aren't gated behind admin/operator availability), and it does not alter who receives funds, only when the already-determined transfer executes.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L838-843)
```text
    }

    /// Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does
    /// not need to be restricted to just the staker or operator.
    public entry fun distribute(
        staker: address, operator: address
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L719-740)
```text
    public entry fun distribute(contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        let coins = withdraw_stake(vesting_contract, contract_address);
        let total_distribution_amount = coin::value(&coins);
        if (total_distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

        // Distribute coins to all shareholders in the vesting contract.
        let grant_pool = &vesting_contract.grant_pool;
        let shareholders = &grant_pool.shareholders();
        shareholders.for_each_ref(|shareholder| {
            let shareholder = *shareholder;
            let shares = pool_u64::shares(grant_pool, shareholder);
            let amount = pool_u64::shares_to_amount_with_total_coins(grant_pool, shares, total_distribution_amount);
            let share_of_coins = coin::extract(&mut coins, amount);
            let recipient_address = get_beneficiary(vesting_contract, shareholder);
            aptos_account::deposit_coins(recipient_address, share_of_coins);
        });
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-935)
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

        let old_beneficiary = get_beneficiary(vesting_contract, shareholder);
        let beneficiaries = &mut vesting_contract.beneficiaries;
        if (beneficiaries.contains_key(&shareholder)) {
            let beneficiary = beneficiaries.borrow_mut(&shareholder);
            *beneficiary = new_beneficiary;
        } else {
            beneficiaries.add(shareholder, new_beneficiary);
        };
```
