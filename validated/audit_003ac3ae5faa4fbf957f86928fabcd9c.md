### Title
Single un-registered/opted-out shareholder can revert `distribute()` for an entire staking contract, freezing withdrawals for all other stakers - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::distribute_internal` iterates over **every** shareholder in a `StakingContract`'s `distribution_pool` and pushes funds to each of them via `aptos_account::deposit_coins`. That helper aborts if the recipient account has opted out of unregistered direct coin transfers and has not registered a `CoinStore<AptosCoin>`. Because the loop is not fault-isolated, one griefing shareholder can make the whole `distribute()` call abort, permanently blocking payout of already-unlocked/inactive stake to every other staker and to the operator/beneficiary sharing that same stake pool — an on-chain analog of the "giant pool can't receive ETH" bug (a required-but-missing acceptance path causes funds to be stuck upstream).

### Finding Description
`distribute_internal` withdraws all withdrawable coins from the stake pool and then loops through `distribution_pool.shareholders()`, calling `aptos_account::deposit_coins` for each recipient: [1](#0-0) 

`aptos_account::deposit_coins` will only auto-register an account for a coin if the account allows unregistered/direct transfers; otherwise it aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`: [2](#0-1) 

Any account holder can permissionlessly disable direct/unregistered coin transfers on their own account via `set_allow_direct_coin_transfers(false)` (standard framework entry function) and simultaneously never register a `CoinStore<AptosCoin>`. If that address later becomes a shareholder in a staking contract's `distribution_pool` (e.g. as a staker who requested a partial `unlock_stake`, or as an operator/beneficiary owed commission), the very next call to `distribute()` / `distribute_internal` will abort while trying to pay that shareholder — reverting the entire transaction, including the coins already extracted via `stake::withdraw_with_cap` for *all other* shareholders in the same pool.

Unlike Aptos coin transfers to fresh, never-registered accounts (which normally succeed because `deposit_coins` auto-registers by default), this specific abort path is real and reachable: it fires whenever the target account explicitly opted out of accepting unregistered coins and holds no `CoinStore<AptosCoin>`. Since `distribute` is `public entry fun distribute(staker, operator)` and can be called by anyone (its own doc comment says "does not need to be restricted to just the staker or operator"), the affected pool becomes permanently unable to pay out any of its unlocked/inactive stake — none of the co-shareholders (other stakers who did `unlock_stake`, or the operator's commission distribution) can be paid until the griefing shareholder either registers for AptosCoin or re-enables direct transfers, which the attacker fully controls and need never do.

The exact same pattern is inherited by `vesting::distribute` / `withdraw_stake`, which calls `staking_contract::distribute` internally: [3](#0-2) 

### Impact Explanation
This breaks the "unlock/withdraw paths must not strand value" invariant for `staking_contract` and, transitively, `vesting`. A single unprivileged party who is already a legitimate shareholder of a distribution pool (any staker who called `unlock_stake`, or the operator receiving commission) can grief the entire pool by opting out of direct transfers and never registering `CoinStore<AptosCoin>`. Once triggered, `distribute()` reverts for the whole pool, so all other shareholders' already-unlocked stake and commission become non-withdrawable indefinitely — funds are not stolen but are trapped/non-recoverable until the griefer's coin registration state changes, which only the griefer controls. This qualifies as "Permanent lock or non-recoverable loss of claim rights in stake ... flows" under the specified impact set.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a shareholder existing in the distribution pool who is not registered for `AptosCoin` and has disabled direct transfers, and (b) anyone calling `distribute`/`unlock_stake`/`request_commission`/`switch_operator`, all of which internally invoke `distribute_internal`. Both preconditions are reachable using only standard, permissionless framework entry functions (`register`/`set_allow_direct_coin_transfers`) — no privileged role is required, and the griefer only needs to control their own account settings.

### Recommendation
In `distribute_internal`, make the per-recipient payout fault-tolerant: use a deposit path that cannot abort the whole loop (e.g., a `try`-style transfer, falling back to crediting the recipient in an escrow/claimable structure) instead of an unconditional `aptos_account::deposit_coins` call inside a loop over all shareholders. Alternatively, force `coin::register` calls at distribution-pool-entry time (e.g., when a shareholder receives a distribution share) so accounts cannot later opt out and starve the whole pool, or process each shareholder's payout in its own isolated, non-aborting step so one recipient's misconfiguration cannot block payouts to the rest.

### Proof of Concept
1. Staker `S` creates a staking contract with operator `O`, `create_staking_contract`. Stake pool accrues rewards.
2. `S` calls `unlock_stake` for a partial amount — this adds `S` as a shareholder of `distribution_pool` via `add_distribution` (see `staking_contract.move` L697-728).
3. Separately, staker `S` (or any other account that will become a shareholder, e.g. the operator via commission) calls `aptos_account::set_allow_direct_coin_transfers(false)` on its own account and never calls `coin::register<AptosCoin>`.
4. Time passes until the unlocked stake becomes inactive/withdrawable.
5. Anyone calls `staking_contract::distribute(staker, operator)`. `distribute_internal` withdraws all inactive+pending_inactive coins, then in the shareholder loop reaches `S` and calls `aptos_account::deposit_coins(S, ...)`, which hits the `assert!(can_receive_direct_coin_transfers(to), ...)` check and aborts (`aptos_account.move` L123-127).
6. The entire `distribute` transaction reverts — no shareholder (including any other, cooperative staker or the operator) receives their already-unlocked funds, and this repeats on every subsequent call to `distribute`/`unlock_stake`/`request_commission`/`switch_operator` for that pool until `S`'s account state is fixed, something only `S` controls.

I was not able to fully trace whether any newer safeguard (e.g., an alternate "skip-and-continue" payout path) exists elsewhere in the framework outside the files reviewed; the loop and `deposit_coins` call shown above are the current implementation in this repo snapshot.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-901)
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1071-1078)
```text
    fun withdraw_stake(vesting_contract: &VestingContract, contract_address: address): Coin<AptosCoin> {
        // Claim any withdrawable distribution from the staking contract. The withdrawn coins will be sent directly to
        // the vesting contract's account.
        staking_contract::distribute(contract_address, vesting_contract.staking.operator);
        let withdrawn_coins = coin::balance<AptosCoin>(contract_address);
        let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
        coin::withdraw<AptosCoin>(contract_signer, withdrawn_coins)
    }
```
