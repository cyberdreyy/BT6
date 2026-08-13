### Title
`withdraw_all` unconditionally burns marginfi shares/closes the balance even when the JupLend dust branch skips the CPI, orphaning real fToken value on a socialized-loss-driven exchange-price drop - (File: programs/marginfi/src/instructions/juplend/withdraw.rs)

### Summary
When `withdraw_all=true`, `juplend_withdraw` first calls `bank_account.withdraw_all(...)` to fully close the user's marginfi position and compute `token_amount` from the current `token_exchange_price` [1](#0-0) . If `token_exchange_price` has fallen below `1e12` (e.g., due to a JupLend socialized-loss event), `expected_assets_for_redeem_from_rate` can floor to `0` for small positions, and the code explicitly treats `token_amount == 0` as a branch that skips the JupLend CPI entirely, setting `received_underlying = 0` [2](#0-1) . Because `bank_account.withdraw_all()` already mutated the in-memory marginfi ledger (closing the balance / removing shares) before this branch is evaluated, the position is fully closed on marginfi's side while the corresponding fTokens are never burned in the real JupLend vault, permanently orphaning that value.

### Finding Description
The code comment itself acknowledges the precondition explicitly: "Socialized loss reducing JupLend's exchange price below 1e12" is a real, non-hypothetical trigger for this branch, not dead code as the surrounding prose otherwise claims [3](#0-2) . The unit test `assets_for_redeem_tiny_position_can_floor_to_zero` in the same file confirms `expected_assets_for_redeem_from_rate(1, 500_000_000_000) == 0`, i.e. a `price < 1e12` reliably produces `token_amount == 0` for small f-token balances [4](#0-3) .

The sequencing problem: `bank_account.withdraw_all(in_receivership)` is invoked and mutates marginfi's internal ledger (closing the balance, zeroing shares) inside the first scoped block, before the dust check that decides whether to run the CPI [5](#0-4) . The dust check (`token_amount == 0`) then causes the function to skip `cpi_juplend_withdraw` entirely and set `received_underlying = 0`, so no fTokens are burned in the real JupLend `integration_acc_2` vault and no underlying is transferred to the user [6](#0-5) . The event emitted afterward reports `amount: received_underlying` (0) and `close_balance: withdraw_all` (true), confirming the balance row is closed for zero real token movement [7](#0-6) .

The net effect: the fTokens backing this position remain in `integration_acc_2` un-burned and unaccounted for by any marginfi balance, while marginfi's internal shares/ledger show the position fully withdrawn. This creates a durable desync between marginfi's tracked total bank shares and the actual custodied JupLend fToken balance — the "shortfall" is neither returned to the user nor re-attributed to remaining depositors' shares.

### Impact Explanation
This is a scoped ledger/vault desync: marginfi's per-user accounting no longer reflects a claim that still exists in real custody, and no other account's shares increase to compensate, so the value becomes permanently stranded in the vault. This matches the flagged "understated debt/collateral desync between marginfi ledger and real JupLend fToken balance" impact. While the individual dust amount is bounded (only reachable for very small `f_tokens_balance`, since price must fall below `1e12` and even then only sub-unit amounts round to zero), it represents a genuine invariant violation: `withdraw_all` closes a balance with non-zero share value without performing the compensating CPI or accounting for the shortfall elsewhere, which the trident-tests `Withdraw (success)` hop-conservation invariant should catch. The user calling this loses their own dust (bounded, self-harming for the attacker), but it also demonstrates that the ledger-state mutation is not atomic with the CPI outcome, which is a genuine defect in the withdraw flow's invariant guarantees, not merely a rounding inconvenience.

### Likelihood Explanation
The trigger (JupLend socialized loss dropping `token_exchange_price` below `1e12`) is an external, permissionless-observable event outside marginfi's control, consistent with the audit rules' scope (this is not oracle bad data or admin action — it is the integrated protocol's own accounting parameter). Once observed, any unprivileged holder of a small JupLend position can simply call `juplend_withdraw` with `withdraw_all=true`; no special timing/race beyond observing the already-decreased on-chain price is required (the condition persists until it recovers, it's not a narrow race window). Because the affected amount is inherently tiny (sub-1-unit dust) and requires a real socialized-loss event upstream, this is a real but low-severity, narrow-magnitude bug rather than a scalable drain — it is not repeatable at scale by a single account beyond its own dust, and each affected account can only trigger the skip once (position is closed).

### Recommendation
Do not perform the ledger-closing mutation (`bank_account.withdraw_all`) before it's known whether the CPI will be skipped, or make the effect conditional/reversible: if `token_amount == 0` after recomputation, either (a) fail the instruction (reject `withdraw_all` when the redeemable amount is zero, requiring the user to wait or accept a non-zero withdrawal), or (b) route the shortfall value by re-crediting it as a socialized loss adjustment against the bank's asset share value (so remaining depositors' share value reflects the reduced backing), consistent with how socialized loss is otherwise handled in the protocol. At minimum, add an explicit accounting event/adjustment so the orphaned shares are not silently unaccounted for.

### Proof of Concept
Rust integration test plan (in the `programs/marginfi` trident/integration test suite, mocking `juplend_mocks`):
1. Set up a JupLend bank and deposit a minimal user position (`f_tokens_balance = 1`) with initial `token_exchange_price = 1_000_000_000_000` (1e12).
2. Force a mock JupLend socialized-loss update to drop `token_exchange_price` below `1e12` (e.g., `500_000_000_000`), simulating the external event via the `JuplendLending` mock account state (no marginfi admin action).
3. Call `juplend_withdraw` with `withdraw_all = Some(true)` from the unprivileged position owner.
4. Assert: `token_amount == 0`, `cpi_juplend_withdraw` is never invoked (mock CPI call counter stays at 0), `received_underlying == 0`.
5. Assert the marginfi balance row for this bank/account no longer exists (`sort_balances`/lookup returns `None`) despite `shares_to_burn > 0` having been computed pre-dust-check.
6. Assert `integration_acc_2` (fToken vault) balance is unchanged (fTokens not burned), demonstrating a shares/vault mismatch: sum of remaining marginfi bank asset shares no longer reconciles with the real fToken vault balance minus other tracked positions.
7. Expected failing assertion under current code: balance closed with `shares_to_burn > 0` and no compensating vault/ledger adjustment — violating the `Withdraw (success)` hop-conservation invariant.

### Citations

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L118-152)
```rust
        let (token_amount, shares_to_burn, share_amount) = if withdraw_all {
            // `withdraw_all` returns the user's full position amount and marginfi share delta.
            let (f_tokens_balance, share_amount) = bank_account.withdraw_all(in_receivership)?;
            // Redeemable underlying = floor(shares * price / 1e12)
            // Then recalculate shares_to_burn from token_amount to guarantee we match
            // JupLend's expected burn amount (should be identical, but this is safer).
            let (token_amount, shares_to_burn) = {
                let token_amount = expected_assets_for_redeem_from_rate(
                    f_tokens_balance,
                    lending.token_exchange_price,
                )
                .ok_or_else(|| error!(MarginfiError::MathError))?;
                let shares_to_burn = expected_shares_for_withdraw_from_rate(
                    token_amount,
                    lending.token_exchange_price,
                )
                .ok_or_else(|| error!(MarginfiError::MathError))?;
                (token_amount, shares_to_burn)
            };

            // Sanity check: recalculated shares should never exceed what we have
            require!(shares_to_burn <= f_tokens_balance, MarginfiError::MathError);

            (token_amount, shares_to_burn, share_amount)
        } else {
            // shares = ceil(assets * 1e12 / token_exchange_price)
            let shares_to_burn = {
                expected_shares_for_withdraw_from_rate(amount, lending.token_exchange_price)
                    .ok_or_else(|| error!(MarginfiError::MathError))?
            };

            let share_amount = bank_account.withdraw(I80F48::from_num(shares_to_burn))?;

            (amount, shares_to_burn, share_amount)
        };
```

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L196-256)
```rust
    // Handle potential dust case where remaining shares are worth less than 1 underlying unit.
    //
    // NOTE: Unlike Drift (which has reachable dust due to double-rounding in its
    // assets → scaled_balance → assets conversion), this case is UNREACHABLE in JupLend
    // under normal operation because:
    //
    // - JupLend uses single-level math: shares = floor(assets * 1e12 / price)
    // - Minimum shares = 1 (u64 integer, not fractional)
    // - Exchange price >= 1e12 (starts at 1:1, only increases with yield)
    // - Therefore: floor(1 * 1e12 / 1e12) = 1 (always at least 1 underlying)
    //
    // Drift's dust is reachable because it uses multi-step rounding:
    // 1. assets → scaled_balance (floor + variable precision per token)
    // 2. scaled_balance + 1 (round up for safety)
    // 3. scaled_balance → assets (floor again)
    // This cascading rounding can produce 0 tokens from small positions.
    //
    // This defensive code exists for potential edge cases:
    // - Socialized loss reducing JupLend's exchange price below 1e12
    // - Future protocol changes affecting share/price invariants
    //
    // If we can guarantee that JupLend's exchange price never drops below 1e12, this branch is dead code.
    let received_underlying = if withdraw_all && token_amount == 0 {
        0
    } else {
        // CPI withdraw: burns fTokens and credits underlying into withdraw intermediary ATA.
        ctx.accounts
            .cpi_juplend_withdraw(token_amount, authority_bump)?;

        let post_withdraw_intermediary_ata_balance =
            accessor::amount(&ctx.accounts.integration_acc_3.to_account_info())?;
        let post_f_token_balance =
            accessor::amount(&ctx.accounts.integration_acc_2.to_account_info())?;

        let received_underlying = post_withdraw_intermediary_ata_balance
            .checked_sub(pre_withdraw_intermediary_ata_balance)
            .ok_or_else(|| error!(MarginfiError::MathError))?;
        require_eq!(
            received_underlying,
            token_amount,
            MarginfiError::JuplendWithdrawFailed
        );

        let burned_shares = pre_f_token_balance
            .checked_sub(post_f_token_balance)
            .ok_or_else(|| error!(MarginfiError::MathError))?;
        require_eq!(
            burned_shares,
            shares_to_burn,
            MarginfiError::JuplendWithdrawFailed
        );

        // Transfer underlying from withdraw intermediary ATA -> destination.
        ctx.accounts
            .cpi_transfer_withdraw_intermediary_ata_to_destination(
                received_underlying,
                authority_bump,
            )?;

        received_underlying
    };
```

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L262-274)
```rust
        emit!(LendingAccountWithdrawEvent {
            header: AccountEventHeader {
                signer: Some(ctx.accounts.authority.key()),
                marginfi_account: ctx.accounts.marginfi_account.key(),
                marginfi_account_authority: marginfi_account.authority,
                marginfi_group: marginfi_account.group,
            },
            bank: bank_key,
            mint: bank_mint,
            amount: received_underlying,
            share_amount: share_amount.into(),
            close_balance: withdraw_all,
        });
```

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L626-630)
```rust
    fn assets_for_redeem_tiny_position_can_floor_to_zero() {
        // floor(1 * 0.5e12 / 1e12) = floor(0.5) = 0
        let assets = expected_assets_for_redeem_from_rate(1, 500_000_000_000).unwrap();
        assert_eq!(assets, 0);
    }
```
