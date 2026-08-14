### Title
Permanent `ACCOUNT_DISABLED` flag set by `handle_bankruptcy` freezes an account's unrelated collateral in other banks with no recovery path - (File: `programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs`)

### Summary
The gogopool report describes a Multisig-driven state transition (`recordStakingError()` → `finishFailedMinipoolByMultisig()`) that lands a minipool in a "finished" state that `withdrawMinipoolFunds()` never accepts, permanently trapping the node operator's funds because the state machine has no valid transition out of that terminal state. The analogous bug class in marginfi is a one-way, irreversible account-level flag transition that blocks withdrawal permanently, without guaranteeing the account has no remaining value to withdraw.

### Finding Description
`lending_pool_handle_bankruptcy` settles bad debt for **one specific bank's liability balance** on a `MarginfiAccount`, then unconditionally sets `ACCOUNT_DISABLED` on the entire account: [1](#0-0) 

This flag is checked as a hard blocker in every user-facing balance-mutating instruction, including `LendingAccountWithdraw`: [2](#0-1) 

Crucially, `set_flag`/`unset_flag` exist generically on `MarginfiAccountImpl`, but there is no call site anywhere in the codebase that clears `ACCOUNT_DISABLED` once set — the transition is one-directional and permanent: [3](#0-2) 

Per the documentation, this is treated as an FSM terminal state ("Bankrupt" → account "effectively zeroed out and disabled"): [4](#0-3) 

The problem is that a `MarginfiAccount` can hold up to 16 independent balances across different banks. `handle_bankruptcy` is invoked per-bank (it looks up a specific `bank_pk` balance and repays only that bank's bad debt via `BankAccountWrapper::find(...).repay(bad_debt)`), and `socialize_loss` spreads the loss to depositors of *that* bank — it does not touch the account's other balances: [5](#0-4) 

`check_account_bankrupt` only verifies overall account equity is below the bankruptcy threshold (net assets minus liabilities across the whole account), not that every individual balance is empty. This means it is architecturally possible for an account to be "bankrupt" in aggregate net-equity terms while still holding a nonzero collateral position in a bank unrelated to the bad-debt bank being resolved (e.g. a small residual/isolated deposit that wasn't the source of the shortfall, or dust left after socialize_loss/repay rounding). Once `handle_bankruptcy` runs and sets `ACCOUNT_DISABLED`, that account is permanently barred from `LendingAccountWithdraw` (and every other user instruction: deposit, borrow, repay, close_balance, drift/kamino/juplend/solend variants all gate on the same flag), with no admin or protocol instruction available to clear the flag and let the authority retrieve any leftover collateral.

This mirrors the gogopool root cause precisely: a privileged/permissionless maintenance instruction (`finishFailedMinipoolByMultisig` / `lending_pool_handle_bankruptcy`) drives the account into a terminal flag state, and the withdrawal-path guard was written assuming that terminal state implies zero recoverable value — an assumption that isn't provably enforced by the state transition itself.

### Impact Explanation
If the assumption "bankrupt implies zero recoverable balances everywhere" is not enforced at the code level, any leftover value in other bank positions becomes permanently, unrecoverably locked the moment `handle_bankruptcy` executes — a durable freeze of user funds with direct financial effect and no on-chain remediation. Even in the case where `PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG` is set on a bank, this instruction is callable by *any* signer, so a malicious/careless caller triggering bankruptcy resolution on a technically-bankrupt account could inadvertently (or intentionally, from a griefing angle) freeze unrelated dust/collateral that the user could otherwise have withdrawn first.

### Likelihood Explanation
Likelihood is limited by how easy it is in practice to reach the specific state where bankruptcy is triggered while non-bankrupt-bank balances still hold nonzero value (this requires multi-bank positions, specific price paths, and rounding behavior in `socialize_loss`/`repay` that I could not fully trace through to prove a concrete nonzero leftover in this pass). I could not conclusively verify from the available code and tests whether `check_account_bankrupt`/other invariants guarantee all balances are driven to exactly zero across the whole account before/at the point `ACCOUNT_DISABLED` is set, or whether such leftover-balance scenarios are unreachable in practice (e.g., due to prior liquidation/deleverage always fully draining collateral before bankruptcy is reachable). This uncertainty should be resolved by a deeper code/test review before treating this as a confirmed, exploitable issue rather than an architectural analog concern.

### Recommendation
- Before or as part of setting `ACCOUNT_DISABLED` in `lending_pool_handle_bankruptcy`, assert (or enforce) that all other balances on the account are empty/zero, or scope the disable to prevent it from blocking withdrawal of unrelated non-bankrupt-bank collateral.
- Alternatively, add an explicit, permissioned recovery path (e.g., an admin-only instruction) that can sweep or release any residual balances from a disabled/bankrupt account, so no state transition is fully terminal without a corresponding funds-recovery mechanism — directly analogous to the gogopool mitigation, which removed the trapping transition rather than leaving users with no way out.

### Proof of Concept
Not independently reproduced in this pass (no test harness run). The logical PoC path would be:
1. Create a `MarginfiAccount`, deposit collateral in Bank A (small amount) and Bank B (larger amount), borrow against Bank B's collateral to the point the account's aggregate equity falls under `BANKRUPT_THRESHOLD` while Bank A's balance remains active and nonzero.
2. Call `lending_pool_handle_bankruptcy` targeting Bank B's bad debt.
3. Observe `ACCOUNT_DISABLED` is now set on the account (`marginfi_account.set_flag(ACCOUNT_DISABLED, true)`), and any subsequent `lending_account_withdraw` call against the residual Bank A balance fails with `MarginfiError::AccountDisabled`, with no instruction available to clear the flag. [6](#0-5)

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L109-123)
```rust
    let lending_account_balance = marginfi_account
        .lending_account
        .balances
        .iter_mut()
        .find(|balance| balance.is_active() && balance.bank_pk == bank_loader.key());

    check!(
        lending_account_balance.is_some(),
        MarginfiError::LendingAccountBalanceNotFound
    );

    let lending_account_balance = lending_account_balance.unwrap();

    let bad_debt: I80F48 =
        bank.get_liability_amount(lending_account_balance.liability_shares.into())?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L189-207)
```rust
    // Socialize bad debt among depositors.
    let kill_bank = bank.socialize_loss(socialized_loss)?;

    // Settle bad debt.
    // The liabilities of this account and global total liabilities are reduced by `bad_debt`
    BankAccountWrapper::find(
        &bank_loader.key(),
        &mut bank,
        &mut marginfi_account.lending_account,
    )?
    .repay(bad_debt)?;

    // Update bank cache after all manipulations (interest accrual, loss socialization, repay)
    bank.update_bank_cache(group)?;
    bank.update_cache_price(cached_price)?;

    marginfi_account.set_flag(ACCOUNT_DISABLED, true);
    marginfi_account.indexer_flags.has_been_bankrupted = 1;
    marginfi_account.last_update = clock.unix_timestamp as u64;
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L259-276)
```rust
    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        constraint = {
            let acc = marginfi_account.load()?;
            !acc.get_flag(ACCOUNT_DISABLED)
        } @MarginfiError::AccountDisabled,
        constraint = {
            let a = marginfi_account.load()?;
            account_not_frozen_for_authority(&a, authority.key())
        } @ MarginfiError::AccountFrozen,
        constraint = {
            let a = marginfi_account.load()?;
            let g = group.load()?;
            is_signer_authorized(&a, g.admin, authority.key(), true, true)
        } @ MarginfiError::Unauthorized
    )]
    pub marginfi_account: AccountLoader<'info, MarginfiAccount>,
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L64-73)
```rust
pub trait MarginfiAccountImpl {
    fn initialize(&mut self, group: Pubkey, authority: Pubkey, current_timestamp: u64);
    fn set_flag(&mut self, flag: u64, msg: bool);
    fn unset_flag(&mut self, flag: u64, msg: bool);
    fn get_flag(&self, flag: u64) -> bool;
    fn increment_active_orders(&mut self) -> MarginfiResult;
    fn decrement_active_orders(&mut self) -> MarginfiResult;
    fn can_be_closed(&self) -> bool;
    fn sync_indexer_flags(&mut self);
}
```

**File:** guides/DEVELOPERS_INTEGRATORS/ACCOUNT_LIFECYCLE.md (L134-139)
```markdown
### 4. Bankrupt

If an account's equity drops below the bankruptcy threshold ($0.10), it can be handled by the
`HandleBankruptcy` instruction. Bad debt is socialized across lenders via the insurance fund. The
account is effectively zeroed out and disabled.

```

**File:** programs/marginfi/tests/admin_actions/bankruptcy_auth.rs (L214-220)
```rust
    // Check borrower account is disabled and shares are
    let borrower_marginfi_account = borrower_account.load().await;
    assert!(borrower_marginfi_account.get_flag(ACCOUNT_DISABLED));
    assert_eq!(
        borrower_marginfi_account.lending_account.balances[0].liability_shares,
        I80F48!(0.0).into()
    );
```
