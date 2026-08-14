Based on my research, I found a genuine analog to the reported bug class in marginfi's emissions-accrual mechanism, though I was unable to fully verify whether an existing guard covers all closure paths — I'll flag that uncertainty explicitly.

### Title
Closing a bank balance via `withdraw_all`/`repay_all`/`close_balance` discards accrued `emissions_outstanding` without requiring settlement, permanently losing the user's unclaimed emissions — ([File: programs/marginfi/src/state/marginfi_account.rs])

### Summary
The bug class in the report is: an entity accrues a claimable value (payout balance) tied to a position, and fully closing/burning that position wipes the accrued value instead of preserving or forcing settlement first. In marginfi, each `Balance` tracks `emissions_outstanding`, the unclaimed emissions accrued for that specific bank position [1](#0-0) . When a position is fully closed — via `withdraw_all`, `repay_all`, or `close_balance` — the code calls `balance.close()` and resets the balance slot without any check that `emissions_outstanding` is zero first [2](#0-1) [3](#0-2) .

### Finding Description
`Balance::empty_deactivated()`, which represents the cleared/closed state of a slot, hardcodes `emissions_outstanding` back to zero [4](#0-3) . The `withdraw_all` and `close_balance` functions in `BankAccountWrapper` call `balance.close()` as soon as the asset/liability checks pass, with no assertion that any accrued `emissions_outstanding` on the balance has already been claimed or is zero [2](#0-1) [3](#0-2) .

Notably, there IS an error variant defined specifically for this scenario — `CannotCloseOutstandingEmissions`, "Cannot close balance because of outstanding emissions" [5](#0-4)  — indicating the protocol authors were aware this needs guarding. However, searching the Rust source for where this error is actually raised turned up matches only in `errors.rs` itself and in test files, not in any instruction/state logic I could locate (`marginfi_account.rs`, `withdraw.rs`, `close_balance` instruction handler). This strongly suggests either the check is dead/unused, or it is implemented in a file/module I was not able to load within my available searches — I could not conclusively confirm which is the case due to indexing limits and iteration constraints. A background Devin session should verify by grepping the full instruction handler for `LendingAccountCloseBalance` and the `lending_account_withdraw`/`lending_account_repay` handlers directly to confirm whether this check is truly missing on any of `withdraw_all`, `repay_all`, or `close_balance`.

The direct analog to the Remora bug is precise: just as burning ALL PropertyTokens of a frozen holder discarded their accrued-but-unclaimed payout eligibility (because the accounting was keyed to a balance that hit zero), fully closing a marginfi `Balance` discards accrued-but-unclaimed `emissions_outstanding` (because `close()` unconditionally zeroes that field) with no requirement that emissions be settled/claimed first.

### Impact Explanation
If confirmed unguarded, this results in a durable, silent loss of user funds: any user with pending unclaimed emissions rewards on a position who calls `lending_account_withdraw` with `withdraw_all = true` (or repays their full liability, or explicitly closes a dust balance) would have those emissions permanently zeroed with no compensation and no recovery path, since the `Balance` slot is deactivated and its fields reset. This is entirely reachable by an ordinary unprivileged account authority in the course of normal usage (no admin/attacker required), making it a quiet fund-loss bug rather than a theoretical edge case.

### Likelihood Explanation
Likelihood is plausible-to-moderate: it requires a user to have both an active emissions campaign accruing rewards on a bank and to trigger a full-close operation (`withdraw_all`, `repay_all`, or manual `close_balance`) before claiming those emissions. Emissions campaigns are an active, documented feature of marginfi [6](#0-5) , and `withdraw_all` is a common, everyday user action (e.g., used throughout the test suite to fully exit a position) [7](#0-6) , making the precondition realistic without any adversarial setup.

### Recommendation
Add an explicit check in `withdraw_all`, `repay_all`, and `close_balance` (mirroring the intent of the already-defined `CannotCloseOutstandingEmissions` error) that reverts the closing operation if `balance.emissions_outstanding` is non-zero, unless emissions have first been claimed/settled to the user in the same instruction. Alternatively, auto-flush any outstanding emissions to the user before zeroing the balance on close, so the value is never silently discarded.

### Proof of Concept
Conceptual reproduction (mirrors the reported PoC structure):
1. Set up a bank with an active emissions campaign; user deposits and accrues `emissions_outstanding` on their `Balance` for that bank [1](#0-0) .
2. Before claiming, the user calls `lending_account_withdraw` with `withdraw_all = true` for that bank [2](#0-1) .
3. `balance.close()` resets the slot including `emissions_outstanding` to zero [4](#0-3) .
4. Assert: the user's previously accrued `emissions_outstanding` is now unrecoverable — no instruction exists to claim rewards from a closed/inactive balance slot, mirroring the Remora scenario where full-token-burn erased the frozen holder's accrued payout entitlement.

**Caveat:** I could not confirm within available tool calls whether the `CannotCloseOutstandingEmissions` error is actually wired into these code paths elsewhere in the codebase (e.g., a separate `close_balance` instruction handler file I did not load). This should be the first thing verified in a follow-up session before treating this as a confirmed, unguarded vulnerability.

### Citations

**File:** type-crate/src/types/user_account.rs (L299-303)
```rust
    pub liability_shares: WrappedI80F48,
    /// Unclaimed emissions rewards for this position
    pub emissions_outstanding: WrappedI80F48,
    /// Unix timestamp (u64) of the last emissions calculation for this position
    pub last_update: u64,
```

**File:** type-crate/src/types/user_account.rs (L347-360)
```rust
    pub fn empty_deactivated() -> Self {
        Balance {
            active: 0,
            bank_pk: Pubkey::default(),
            bank_asset_tag: ASSET_TAG_DEFAULT,
            tag: 0,
            _pad0: [0; 4],
            asset_shares: WrappedI80F48::from(I80F48::ZERO),
            liability_shares: WrappedI80F48::from(I80F48::ZERO),
            emissions_outstanding: WrappedI80F48::from(I80F48::ZERO),
            last_update: 0,
            _padding: [0; 1],
        }
    }
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1623-1648)
```rust
    /// Withdraw existing asset in full - will error if there is no asset.
    /// When `in_receivership` is true, clears the bank's liquidation price cache lock
    /// so that banks whose balances are closed mid-liquidation don't stay permanently locked.
    /// Returns `(spl_withdraw_amount, asset_share_delta)`.
    pub fn withdraw_all(&mut self, in_receivership: bool) -> MarginfiResult<(u64, I80F48)> {
        let balance = &mut self.balance;
        let bank = &mut self.bank;

        let total_asset_shares: I80F48 = balance.asset_shares.into();
        let current_asset_amount = bank.get_asset_amount(total_asset_shares)?;
        let current_liability_amount =
            bank.get_liability_amount(balance.liability_shares.into())?;

        debug!("Withdrawing all: {}", current_asset_amount);

        check!(
            current_asset_amount.is_positive_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::NoAssetFound
        );

        check!(
            current_liability_amount.is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::NoAssetFound
        );

        balance.close()?;
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1737-1767)
```rust
    /// When `in_receivership` is true, clears the bank's liquidation price cache lock
    /// so that banks whose balances are closed mid-liquidation don't stay permanently locked.
    pub fn close_balance(&mut self, in_receivership: bool) -> MarginfiResult<()> {
        let balance = &mut self.balance;
        let bank = &mut self.bank;

        let current_liability_amount =
            bank.get_liability_amount(balance.liability_shares.into())?;
        let current_asset_amount = bank.get_asset_amount(balance.asset_shares.into())?;

        check!(
            current_liability_amount.is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::IllegalBalanceState,
            "Balance has existing debt"
        );

        check!(
            current_asset_amount.is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::IllegalBalanceState,
            "Balance has existing assets"
        );

        let asset_shares: I80F48 = balance.asset_shares.into();
        let liability_shares: I80F48 = balance.liability_shares.into();
        // Counters are incremented in `*_balance_internal` when shares cross
        // `ZERO_AMOUNT_THRESHOLD` upward; match that condition so we don't
        // double-decrement positions that already crossed downward earlier.
        let had_assets = asset_shares.is_positive_with_tolerance(ZERO_AMOUNT_THRESHOLD);
        let had_liabs = liability_shares.is_positive_with_tolerance(ZERO_AMOUNT_THRESHOLD);

        balance.close()?;
```

**File:** programs/marginfi/src/errors.rs (L71-72)
```rust
    #[msg("Cannot close balance because of outstanding emissions")] // 6033
    CannotCloseOutstandingEmissions,
```

**File:** guides/USER/EMISSIONS.md (L7-21)
```markdown
Some banks, including banks from other venues, might offer a token incentive to depositors, or even
to borrowers. We call a set of incentives over time a "Campaign".

For example, a Campaign might distribute 7 tokens of A to lenders per week (one per day). Each
lender's share is determined on a pro-rata basis in real time. If there are two lenders, each
depositing the same amount, then each will be 3.5 tokens per week.

Now let's say there are two users, the first one has \$1 in deposits. User 2 deposits \$1 on
Thursday, and \$5 more on Saturday. This means User 1 and 2 both get 0.5 tokens/day on Thursday and
Friday. On Saturday and beyond, User 1 gets $1/(1+6)= 0.143$ tokens, and User 2 gets $6/(1+6)=0.857$
tokens/day.

Emissions/incentives are delivered by airdrop to the Account's authority, typically on Wednesday, in
no particular order. In the above example, User 1 would get $0.5 + 0.5 * 0.143 * 5 = 1.715$ tokens
and User 2 would get $0.5 + 0.5 + 0.857 * 5 = 5.285$ tokens
```

**File:** tests/specs/basic/09_withdraw_repay.spec.ts (L377-399)
```typescript
    const tx = new Transaction().add(
      await withdrawIx(user.mrgnProgram, {
        marginfiAccount: userAccKey,
        bank: bank,
        tokenAccount: user.tokenAAccount,
        remaining,
        amount: withdrawAmountTokenA_native,
        withdrawAll: true,
      })
    );
    const result = await processBankrunTransaction(bankrunContext, tx, [
      user.wallet,
    ]);
    const events = parseMarginfiEvents(program, result.logMessages);
    const withdrawEvent = events.find(
      (e) => e.name === "lendingAccountWithdrawEvent"
    );
    assert.isDefined(withdrawEvent, "Expected lendingAccountWithdrawEvent");
    // withdrawAll closes the balance, so share_amount is the full pre-close asset shares
    assertI80F48Approx(
      withdrawEvent!.data.shareAmount,
      balancesBefore[0].assetShares
    );
```
