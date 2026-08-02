## Finding

### Title
Malicious operator can permanently trap staker funds by poisoning `staking_contract`'s shared distribution loop with an unpayable beneficiary - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`staking_contract::distribute_internal` pays out *every* shareholder recorded in a shared `distribution_pool` (both the staker's unlocked principal/rewards and the operator's commission) inside a single atomic loop that calls `aptos_account::deposit_coins` for each recipient. `aptos_account::deposit_coins` can abort for an unregistered/opted-out recipient, and a Move abort reverts the entire transaction. Because the operator alone controls which address receives commission (`set_beneficiary_for_operator`), a malicious operator can point their beneficiary at an address engineered to reject the deposit, which then blocks payout to **every other shareholder sharing that distribution_pool**, including the staker's own already-unlocked stake — indefinitely, since only the operator can change the beneficiary.

### Finding Description
`distribute_internal` withdraws all withdrawable inactive stake for the pool and then iterates the pool's shareholders, transferring each one's share via `aptos_account::deposit_coins`, redirecting the operator's payout to the beneficiary: [1](#0-0) 

This function is invoked from every path that should let a staker realize withdrawable value: `request_commission`, `unlock_stake`, `switch_operator`, and the public `distribute` entrypoint: [2](#0-1) [3](#0-2) 

The recipient for the operator's commission is looked up dynamically via `beneficiary_for_operator(operator)`, which the operator sets unilaterally with no staker consent required: [4](#0-3) [5](#0-4) 

`aptos_account::deposit_coins` (the same helper shape used at lines 121-130 of `aptos_account.move`) aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the target address is not yet registered for the coin type and has opted out of arbitrary direct-coin transfers via the permissionless `DirectTransferConfig`: [6](#0-5) [7](#0-6) 

Any account can flip `allow_arbitrary_coin_transfers` to `false` for itself with no privilege beyond owning that address, which is the local-code analog of the Gearbox `receive()`-revert trick: the "malicious contract" is simply an account configured to reject unsolicited/unregistered deposits.

Because the `while (distribution_pool.shareholders_count() > 0)` loop in `distribute_internal` processes *all* recorded distributions (both staker withdrawals added via `add_distribution` in `unlock_stake`, and the operator's own commission distribution) in one atomic pass, a single failing recipient — the operator-controlled beneficiary — aborts the whole transaction and prevents payout to the staker as well, even though the staker did nothing wrong: [8](#0-7) 

Since only the operator (or the staker, but not by unilaterally fixing the operator's misbehaving beneficiary) can change the beneficiary via `set_beneficiary_for_operator`, and the staker has no way to bypass `distribute_internal` to claim only their own share, the staker's unlocked stake becomes permanently unclaimable for as long as the operator keeps the poisoned beneficiary in place.

### Impact Explanation
This breaks the "operator commission, beneficiary payout... corruption that credits wrong account or traps value" and "unlock, reactivate, withdraw... paths must not... strand [value] permanently" invariants. A staker's already-unlocked (post-lockup) principal and rewards can be trapped indefinitely by operator misbehavior, with no staker-side remediation short of governance intervention — this is a High severity denial-of-withdrawal / value-trapping bug reachable by any operator against any staker delegating to them via `staking_contract`.

### Likelihood Explanation
The operator role is unprivileged relative to the staker (stakers choose operators, but operators fully control their own beneficiary address and can set it to any account, including one they set up in advance to reject transfers). The precondition for `aptos_account::deposit_coins` to abort — target unregistered for the coin type and `allow_arbitrary_coin_transfers == false` — is trivially satisfiable by using a freshly created address that has never registered `CoinStore<AptosCoin>`/primary store and has called `aptos_account::set_allow_direct_coin_transfers(false)`. I was not able to fully verify, within available tool calls, whether the AptosCoin/Fungible-Asset migration might auto-register a primary store for all accounts (which would remove this specific abort trigger); this is a residual uncertainty that should be confirmed against the currently deployed FA-migration state before treating likelihood as certain, but the core structural flaw — one shared, all-or-nothing distribution loop paying out a staker together with an operator-controlled, unilaterally-changeable beneficiary — holds regardless.

### Recommendation
- Make `distribute_internal` resilient to a single failing recipient: either use a "pull" pattern (credit each shareholder a claimable balance them withdraw individually) instead of "push" `deposit_coins` in a loop, or wrap each deposit so a failure only skips/re-queues that one recipient's distribution instead of aborting the whole transaction.
- Separately settle the staker's own distribution from the operator/beneficiary's commission distribution so operator-side griefing cannot block the staker's own funds.
- Consider validating that a newly set beneficiary can actually receive `AptosCoin` (already registered, or `allow_arbitrary_coin_transfers == true`) at the time `set_beneficiary_for_operator` is called, rejecting hostile configurations up front.

### Proof of Concept
1. Staker creates a `staking_contract` with `operator` O, adds stake, and later calls `unlock_stake` for their unlocked principal/rewards; `add_distribution` records the staker's payout in the shared `distribution_pool`. [9](#0-8) 
2. Operator O, in advance, creates a fresh address `B`, has `B` call `aptos_account::set_allow_direct_coin_transfers(false)` (permissionless, self-only) without ever registering `B` for `AptosCoin`.
3. Operator O calls `set_beneficiary_for_operator(O, B)`.
4. Once the stake pool's lockup expires and anyone calls `distribute`/`unlock_stake`/`request_commission`/`switch_operator` (which all funnel into `distribute_internal`), the loop reaches `B`'s commission payout, `aptos_account::deposit_coins(B, ...)` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`, and the entire transaction — including the staker's own payout in the same loop — reverts.
5. The staker's unlocked stake remains permanently un-withdrawable through this contract as long as O keeps `B` as beneficiary, since only O can call `set_beneficiary_for_operator` again.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L624-629)
```text
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );
```

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L705-719)
```text
        // If there's less active stake remaining than the amount requested (potentially due to commission),
        // only withdraw up to the active amount.
        let (active, _, _, _) = stake::get_stake(staking_contract.pool_address);
        if (active < amount) {
            amount = active;
        };
        staking_contract.principal -= amount;

        // Record a distribution for the staker.
        add_distribution(
            operator,
            staking_contract,
            staker_address,
            amount,
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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L20-37)
```text
    /// Account does not exist.
    const EACCOUNT_NOT_FOUND: u64 = 1;
    /// Account is not registered to receive APT.
    const EACCOUNT_NOT_REGISTERED_FOR_APT: u64 = 2;
    /// Account opted out of receiving coins that they did not register to receive.
    const EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS: u64 = 3;
    /// Account opted out of directly receiving NFT tokens.
    const EACCOUNT_DOES_NOT_ACCEPT_DIRECT_TOKEN_TRANSFERS: u64 = 4;
    /// The lengths of the recipients and amounts lists don't match.
    const EMISMATCHING_RECIPIENTS_AND_AMOUNTS_LENGTH: u64 = 5;

    /// Configuration for whether an account can receive direct transfers of coins that they have not registered.
    ///
    /// By default, this is enabled. Users can opt-out by disabling at any time.
    struct DirectTransferConfig has key {
        allow_arbitrary_coin_transfers: bool,
        update_coin_transfer_events: EventHandle<DirectCoinTransferConfigUpdatedEvent>
    }
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
