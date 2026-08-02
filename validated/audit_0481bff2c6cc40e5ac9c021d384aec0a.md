### Title
Permissionless DoS of stake/commission distribution via `DirectTransferConfig` opt-out (Aptos analog of ERC20 blocklist griefing) - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
The reNFT bug is a case where an external, attacker-controlled account state (ERC20 blocklist) can be weaponized to make a *shared* settlement transaction permanently revert, freezing funds/assets belonging to unrelated parties. Aptos has a structurally identical primitive: `aptos_account::deposit_coins()` (and the underlying `coin::deposit`) aborts when the recipient (a) has never registered a `CoinStore<AptosCoin>` and (b) has explicitly opted out of unsolicited transfers via `aptos_account::set_allow_direct_coin_transfers(false)`. This opt-out is fully self-service and unprivileged — exactly like becoming "blocklisted" is self-inflicted/attacker-controlled in the ERC20 case, except here it's cheaper: no third-party issuer cooperation is needed at all.

`staking_contract::distribute_internal` (and the analogous `vesting::distribute`) pay out unlocked/inactive stake to **multiple recipients in one shared loop** (staker, operator, and — via `beneficiary_for_operator` — an operator-chosen beneficiary address that can be arbitrary and previously unregistered). Because the loop performs an unconditional `aptos_account::deposit_coins` for every recipient inside a single atomic transaction, **one recipient's opt-out permanently blocks payout to all other recipients sharing that distribution pool**, and this permissionless `distribute` entry function can be called by anyone, but always aborts the same way.

### Finding Description
`distribute_internal` in `staking_contract.move` withdraws all inactive/pending-inactive stake and pays it out to every current shareholder of the `distribution_pool` in one loop: [1](#0-0) 

Note that if `recipient == operator`, the payout is redirected to `beneficiary_for_operator(operator)` — an address chosen unilaterally by the operator via `set_beneficiary_for_operator`, which does **not** need to be a pre-existing/registered account: [2](#0-1) 

`aptos_account::deposit_coins` will attempt to auto-register the recipient for `AptosCoin` if they have no `CoinStore`, but only if the recipient has not opted out of direct coin transfers; if they have, it aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`: [3](#0-2) 

Any account can flip this opt-out flag on itself, permissionlessly, at any time, by calling `aptos_account::set_allow_direct_coin_transfers(false)` (evidenced by `can_receive_direct_coin_transfers` reading `DirectTransferConfig.allow_arbitrary_coin_transfers`, referenced in the spec at `aptos_account.spec.move` lines 166–173). This is the exact functional analog of an ERC20 issuer blocklisting an address — except the "blocklist" here is entirely self-controlled by the attacker and requires zero third-party cooperation.

Because `distribute_internal`/`distribute` (and `vesting::distribute`, which distributes among a multi-shareholder `grant_pool` the same way) redeem shares from the pool and then deposit in the *same* atomic transaction for every shareholder in the loop, a revert on any single recipient's deposit reverts the entire transaction — undoing share redemption for everyone and leaving the whole `distribution_pool` (and vesting contract) permanently unable to pay out until that one address re-enables direct transfers.

`distribute` is intentionally permissionless ("Allow anyone to distribute already unlocked funds"): [4](#0-3) 

so nobody can bypass or route around the blocked recipient — every future `distribute()` call for that staking contract / vesting contract will hit the same recipient and revert identically.

### Impact Explanation
This breaks the "unlock/reactivate/withdraw paths must not strand value permanently" invariant for `staking_contract` (and by extension `vesting`, since `vesting::distribute` also pays out via `aptos_account::deposit_coins`/`staking_contract::distribute` under the hood):
- A single delegator/shareholder or an operator-chosen beneficiary who opts out of direct transfers permanently blocks distribution of **all** other shareholders' inactive stake and commission in that staking contract or vesting pool.
- The stake remains withdrawn from the stake pool into `distribution_pool`'s internal accounting (shares already bought/updated) but can never be paid out while the loop keeps hitting the blocked address, since `distribute_internal` always processes the full shareholder set in one transaction.
- This traps the staker's principal-adjacent rewards and operator commission indefinitely, corresponding to "permanent lock or non-recoverable loss of claim rights ... commission, beneficiary ... vesting flows."

### Likelihood Explanation
High feasibility for the attacker (self-griefing or griefing a shared vesting/staking pool) and no privileged capability is required:
- `set_allow_direct_coin_transfers` is a standard, unprivileged, self-callable entry function.
- Any operator can set an arbitrary, previously-unregistered address as their beneficiary via `set_beneficiary_for_operator`, and that beneficiary address could pre-emptively (or maliciously in collusion with an operator who wants to hold a staker's funds hostage) opt out of direct transfers before ever being paid.
- Vesting contracts by design support multiple shareholders sharing a single `distribution_pool`/`grant_pool`, so the blast radius (other legitimate shareholders locked out) is real and not merely self-harm.

### Recommendation
- Make payout iteration resilient to individual failures: wrap each recipient's `aptos_account::deposit_coins` call so a failure for one recipient does not abort payouts to the rest (e.g., catch/skip and re-credit failed amounts back into the distribution pool or a per-recipient claimable balance, analogous to a pull-payment pattern).
- Alternatively, use a non-reverting deposit primitive (e.g., an internal deposit that bypasses `can_receive_direct_coin_transfers` for framework-owned distribution flows, since it is not an unsolicited "direct transfer" but a due payout of already-earned stake), or force `coin::register` unconditionally before deposit for owed payouts, since consent to receive dividends of stake you are entitled to should not be overridable by a third-party opt-out flag.
- Split `distribute_internal`'s "redeem shares" and "transfer coins" into separate per-recipient pull-based claim functions so failure of one recipient's claim cannot block others.

### Proof of Concept
Conceptual PoC (not run, based on code tracing):
1. Staker creates a `staking_contract` with an operator; operator sets `beneficiary_for_operator` to a freshly created address `B` that has never registered `CoinStore<AptosCoin>`.
2. `B` calls `aptos_account::set_allow_direct_coin_transfers(false)` (self-service, unprivileged).
3. Stake pool accrues rewards/commission; commission is unlocked via `request_commission` and lockup passes so funds become inactive/withdrawable.
4. Anyone calls `staking_contract::distribute(staker, operator)`. `distribute_internal` withdraws all inactive coins, updates the distribution pool, and iterates shareholders. When it reaches `operator`'s payout, it redirects to `beneficiary_for_operator(operator) == B`, and `aptos_account::deposit_coins(B, ...)` hits the `!can_receive_direct_coin_transfers(B)` branch and aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. The entire `distribute` transaction reverts. If the staker (or other shareholders in a `vesting::distribute` scenario with multiple shareholders sharing one pool) were also owed a payout in the same call, their funds remain stuck too, and every subsequent retry of `distribute` fails identically until `B` re-enables direct transfers.

This could not be executed in a live environment as part of this analysis (no test harness run), so the exact abort code path should be confirmed by writing a Move unit test in `staking_contract.move`'s test module mirroring the existing `test_operator_can_set_beneficiary` test but adding a `set_allow_direct_coin_transfers(false)` call for the beneficiary before `distribute()`.

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
