Based on my research, I found a solid analog to the ALCX "rewards not claimed on merge" bug in marginfi-v2's `close_balance` logic.

### Title
Unclaimed emissions permanently lost when a Balance is closed via `close_balance` - ([File: programs/marginfi/src/state/marginfi_account.rs])

### Summary
`BalanceImpl::close_balance` (used by both `LendingAccountCloseBalance` and the receivership/liquidation-related paths) zeroes out a user's `Balance` slot once asset and liability shares are empty, but it never checks or settles `balance.emissions_outstanding` first. The codebase even defines a dedicated error, `MarginfiError::CannotCloseOutstandingEmissions`, for exactly this scenario, but that error is never actually raised anywhere in the instruction logic — it is dead code.

### Finding Description
In `close_balance`, the function only validates that `current_liability_amount` and `current_asset_amount` are zero before invoking `balance.close()`: [1](#0-0) 

`Balance` carries a dedicated `emissions_outstanding` field representing unclaimed emissions rewards accrued for that specific position: [2](#0-1) 

and `Balance::empty_deactivated()` (the same reset pattern `close()` follows) resets `emissions_outstanding` back to zero: [3](#0-2) 

The protocol clearly anticipated this exact hazard: `MarginfiError::CannotCloseOutstandingEmissions` ("Cannot close balance because of outstanding emissions") is defined and mapped in the error table, but it is never actually invoked by any instruction handler in the codebase: [4](#0-3) [5](#0-4) 

The public entrypoint `LendingAccountCloseBalance`/`close_balance` (via `p0-cli`'s `marginfi_account_close_balance`) is directly reachable by any account authority for a zero-balance position — exactly analogous to how `VotingEscrow::merge` in the ALCX report allowed a token with unclaimed rewards to be destroyed without settling those rewards first: [6](#0-5) 

This mirrors the reported bug class precisely: a state-destroying operation (burning a veALCX token / closing a marginfi Balance) proceeds without first flushing/claiming an accrued-but-unclaimed reward balance tied to that specific state object, permanently erasing it.

### Impact Explanation
Any accrued emissions in `Balance.emissions_outstanding` for a position are irrecoverably wiped the moment a user (or the protocol, via liquidation/bankruptcy-driven `close_balance` calls) closes that balance. Since there is no code path that reads `emissions_outstanding` and forces a settlement/claim before `close_balance` executes, and the intended guard (`CannotCloseOutstandingEmissions`) is unused, users lose real, already-earned emissions tokens permanently. This is a durable freezing/loss of yield with financial effect, matching the "Permanent freezing of unclaimed yield" impact category from the external report.

### Likelihood Explanation
This requires no privileged access — any account authority holding a fully-repaid/withdrawn Balance with unclaimed emissions can trigger it simply by calling the standard `lending_account_close_balance` instruction (exposed via `p0 account close-balance`). It can also occur incidentally any time a user repays/withdraws in full and then closes the balance without a separate emissions-claim step in between, making it easy to trigger unintentionally, not just via a crafted attack.

### Recommendation
Before permitting `close_balance` to zero out a `Balance`, check `balance.emissions_outstanding` and either (a) require it to be zero (surfacing the already-defined `CannotCloseOutstandingEmissions` error), or (b) auto-settle/transfer the outstanding emissions to the user/emissions destination account as part of the close flow, similar to how `withdraw` flows in the ALCX report were expected to auto-claim rewards before burning state.

### Proof of Concept
Conceptual reproduction path (mirroring the ALCX PoC structure):
1. User deposits into a bank that has emissions configured (`emissions_rate` > 0), accruing `emissions_outstanding` on their `Balance`.
2. User fully withdraws/repays the position (`current_asset_amount == 0`, `current_liability_amount == 0`), leaving `emissions_outstanding` > 0 on the still-open Balance slot.
3. User calls `lending_account_close_balance` (or it is triggered indirectly via bankruptcy/receivership settlement).
4. `close_balance` succeeds because it only checks asset/liability amounts [7](#0-6) , then calls `balance.close()`, resetting `emissions_outstanding` to zero.
5. There is no subsequent instruction able to recover the wiped `emissions_outstanding` value — it is permanently lost, exactly as ALCX rewards became permanently unclaimable after `merge` burned the "from" token.

### Citations

**File:** programs/marginfi/src/state/marginfi_account.rs (L1739-1767)
```rust
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

**File:** type-crate/src/types/user_account.rs (L294-306)
```rust
    /// The user's asset (deposit) shares in the bank. Multiply by `bank.asset_share_value` for
    /// the token amount.
    pub asset_shares: WrappedI80F48,
    /// The user's liability (borrow) shares in the bank. Multiply by `bank.liability_share_value`
    /// for the token amount.
    pub liability_shares: WrappedI80F48,
    /// Unclaimed emissions rewards for this position
    pub emissions_outstanding: WrappedI80F48,
    /// Unix timestamp (u64) of the last emissions calculation for this position
    pub last_update: u64,
    /// Reserved for future use
    pub _padding: [u64; 1],
}
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

**File:** programs/marginfi/src/errors.rs (L71-72)
```rust
    #[msg("Cannot close balance because of outstanding emissions")] // 6033
    CannotCloseOutstandingEmissions,
```

**File:** programs/marginfi/src/errors.rs (L489-489)
```rust
            6033 => MarginfiError::CannotCloseOutstandingEmissions,
```

**File:** p0-cli/src/processor/account.rs (L1144-1172)
```rust
pub fn marginfi_account_close_balance(
    profile: &Profile,
    config: &Config,
    bank_pk: Pubkey,
) -> Result<()> {
    let marginfi_account_pk = profile.get_marginfi_account()?;
    let marginfi_account = config
        .mfi_program
        .account::<MarginfiAccount>(marginfi_account_pk)?;
    ensure_account_unblocked(&marginfi_account, "close-balance")?;

    let ix = Instruction {
        program_id: config.program_id,
        accounts: marginfi::accounts::LendingAccountCloseBalance {
            group: marginfi_account.group,
            marginfi_account: marginfi_account_pk,
            authority: config.authority(),
            bank: bank_pk,
        }
        .to_account_metas(Some(true)),
        data: marginfi::instruction::LendingAccountCloseBalance.data(),
    };

    let signing_keypairs = config.get_signers(false);
    let sig = send_tx(config, vec![ix], &signing_keypairs)?;
    println!("Close balance successful: {sig}");

    Ok(())
}
```
