### Title
Unregistered/opted-out shareholder permanently blocks `distribute()` for all other participants in `staking_contract` and `vesting` pools - (`File: aptos-move/framework/aptos-framework/sources/staking_contract.move`, `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Summary
`staking_contract::distribute_internal` and `vesting::distribute` both iterate over **all** shareholders of a shared distribution pool in a single atomic transaction and pay each one via `aptos_account::deposit_coins`. If a single shareholder's account is unregistered for `AptosCoin` **and** has opted out of direct coin transfers (`aptos_account::set_allow_direct_coin_transfers(false)`), that one deposit aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`, which aborts the whole transaction and therefore blocks payout to every other shareholder/operator sharing that pool — the direct Move analog of the Sherlock M-3 "one failing transfer blocks the whole liquidation" bug class.

### Finding Description
`staking_contract::distribute_internal` loops over the shared `distribution_pool`'s shareholders and calls `aptos_account::deposit_coins` for each: [1](#0-0) 

`vesting::distribute` does the same thing over the vesting contract's `grant_pool.shareholders()`: [2](#0-1) 

`aptos_account::deposit_coins` only auto-registers a `CoinStore`/primary store for the recipient if the recipient has not opted out of direct transfers; if they explicitly disabled that (`can_receive_direct_coin_transfers` returns `false`) while still unregistered, the call aborts: [3](#0-2) 

Critically, neither `staking_contract` nor `vesting` guards against this for ordinary shareholders. `create_vesting_contract` only validates `withdrawal_address` for APT registration, not the individual `shareholders` list that is later used in `distribute()`: [4](#0-3) 

The framework authors were aware of exactly this failure mode for the *beneficiary* path — `set_beneficiary` explicitly enforces registration specifically "so `distribute()` wouldn't fail and block all other accounts from receiving APT if one beneficiary is not registered": [5](#0-4) 

However this protection is not applied to:
1. The initial `shareholders` set at `create_vesting_contract` time, and
2. Any shareholder/staker who is registered (or unregistered) at creation time but later calls `set_allow_direct_coin_transfers(false)` on their own account before ever registering a `CoinStore`/PFS for `AptosCoin`.

The same unguarded pattern applies to `staking_contract::distribute_internal`, which is reused by `distribute`, `unlock_stake`, `switch_operator`, and `reset_lockup` flows — all of which pay out via the same shared `distribution_pool` including both the staker and the operator/beneficiary as recipients: [6](#0-5) 

Since a Move entry-function transaction is atomic, if any single recipient's `deposit_coins` call aborts, the whole transaction — including withdrawal from the stake pool and payment to every other legitimate shareholder/operator — is rolled back. Because the malicious/griefing participant is a permanent member of the shares pool (they cannot be removed without first paying them, which requires the very call that now always reverts), this becomes a persistent, unrecoverable DoS on that specific staking_contract/vesting pool's distribution.

### Impact Explanation
This directly matches required impact categories:
- "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows" — every co-shareholder/operator in the affected `staking_contract` or `vesting_contract` loses the ability to ever withdraw already-unlocked/inactive stake, rewards, or vested grants, since `distribute()`/`distribute_internal()` is the only path to move inactive stake out of the underlying stake pool for these constructs.
- "Operator commission, beneficiary payout ... corruption that ... traps value" — an adversarial staker (or shareholder) sharing the pool can indefinitely withhold the operator's commission payout, since both are paid out atomically from the same loop.

This is high severity because it is triggerable by any unprivileged party who is already a legitimate shareholder/staker in the contract (no special role needed beyond being included as a shareholder, which for `vesting` happens at contract-creation time under admin's discretion but for `staking_contract` any staker is automatically a distribution-pool participant the moment they call `unlock_stake`), and it strands funds belonging to other, non-malicious parties.

### Likelihood Explanation
Likelihood is moderate-to-high: the attacker only needs to (a) never register for `AptosCoin` (the default state for a fresh account, or achievable by simply not calling `coin::register`/never receiving APT before) and (b) call the permissionless `aptos_account::set_allow_direct_coin_transfers(false)` on their own account. Both actions are ordinary, unprivileged, and require no special timing beyond doing so before the first `distribute()` call succeeds for that pool.

### Recommendation
- In `vesting::create_vesting_contract`, assert `assert_account_is_registered_for_apt` (or otherwise guarantee a valid receiving path) for every shareholder in the initial list, not just `withdrawal_address`.
- In both `staking_contract::distribute_internal` and `vesting::distribute`, wrap each per-recipient `aptos_account::deposit_coins` call so that a failure for one recipient does not abort the whole distribution — e.g., catch the failure (via `move_to`/pending-claim escrow, or by skipping that recipient and re-queuing their share) so other shareholders/operators can still be paid in the same call.
- Alternatively, disallow (or explicitly re-check) `set_allow_direct_coin_transfers(false)` for any account that is currently registered as a shareholder in an active `staking_contract` or `vesting_contract` distribution pool.

### Proof of Concept
Conceptual repro (staking_contract):
1. Staker `S` creates a `staking_contract` with operator `O` via `staking_contract::create_staking_contract`.
2. Before ever registering for `AptosCoin` (no `CoinStore`/PFS store exists for `S`), `S` calls `aptos_account::set_allow_direct_coin_transfers(false)`.
3. Time passes; rewards accrue; `S` calls `unlock_stake` (or anyone calls `distribute`), which adds `S` as a distribution-pool recipient via `add_distribution` and, once inactive, invokes `distribute_internal`.
4. `distribute_internal`'s loop reaches recipient `S`; `aptos_account::deposit_coins(S, ...)` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` because `S` is unregistered and has opted out.
5. The whole `distribute`/`unlock_stake`/`switch_operator`/`reset_lockup` transaction reverts. Operator `O`'s pending commission (also queued in the same `distribution_pool`) can never be paid out as long as `S` remains in this state, and `S` cannot be removed from the pool without a successful `distribute_internal` call — creating a permanent deadlock for the pool.

The equivalent sequence applies to `vesting::distribute` using an unregistered shareholder from the `shareholders` vector passed to `create_vesting_contract`.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-878)
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L549-558)
```text
        assert!(
            !system_addresses::is_reserved_address(withdrawal_address),
            error::invalid_argument(EINVALID_WITHDRAWAL_ADDRESS),
        );
        assert_account_is_registered_for_apt(withdrawal_address);
        assert!(shareholders.length() > 0, error::invalid_argument(ENO_SHAREHOLDERS));
        assert!(
            buy_ins.length() == shareholders.length(),
            error::invalid_argument(ESHARES_LENGTH_MISMATCH),
        );
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L730-740)
```text
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
