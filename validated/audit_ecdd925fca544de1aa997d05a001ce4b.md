Confirmed: `drift_deposit` and `drift_withdraw` both require a `group` account with `!group.load()?.is_protocol_paused()` constraint and call `validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState/FailsInReduceState)`, while `DriftClaimBadDebt` has no `group` account at all and `drift_claim_bad_debt` never calls `validate_bank_state`.

### Title
`drift_claim_bad_debt` bypasses group-pause and bank-operational-state checks, allowing permissionless vault-authority CPI fund movement while paused - ([File: programs/marginfi/src/instructions/drift/claim_bad_debt.rs])

### Summary
`drift_claim_bad_debt` signs a CPI to the external Drift merkle-distributor program and then transfers the claimed tokens out of the `liquidity_vault_authority` PDA, but unlike `drift_deposit`/`drift_withdraw` it takes no `marginfi_group` account and never calls `validate_bank_state`. Any unprivileged caller can therefore invoke this instruction even after the group is paused or the bank is set to `Paused`/`ReduceOnly`/`KilledByBankruptcy`.

### Finding Description
`drift_deposit` and `drift_withdraw` both gate fund movement on two checks: an Anchor account constraint `!group.load()?.is_protocol_paused()` on the `group: AccountLoader<'info, MarginfiGroup>` account [1](#0-0)  and an explicit `validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)` call inside the handler [2](#0-1) .

`DriftClaimBadDebt`'s account struct contains no `group` account at all — only `bank`, `fee_state`, `liquidity_vault_authority`, and Drift/distributor-related accounts [3](#0-2) . The handler `drift_claim_bad_debt` never calls `validate_bank_state`, so it does not check `bank.config.operational_state` for `Paused`, `ReduceOnly`, or `KilledByBankruptcy` [4](#0-3) . Yet the instruction still signs with the bank's `liquidity_vault_authority` PDA (via `bank_signer!`) to CPI into the external merkle-distributor program (`cpi_new_claim`) and then to transfer the claimed tokens to `destination_token_account` (`cpi_transfer_to_destination`) [5](#0-4) . Because `validate_bank_state`/`ProtocolPaused` guards defined in `utils/general.rs` [6](#0-5)  are absent here, this instruction is reachable by any permissionless payer regardless of the group's pause state or the bank's operational state.

### Impact Explanation
Marginfi's pause mechanism (`is_protocol_paused` on `MarginfiGroup`, and `BankOperationalState::Paused`/`KilledByBankruptcy`) is meant to be a uniform emergency stop for all vault-adjacent operations across every integration (Drift, Kamino, Solend, JupLend) during incident response. `drift_claim_bad_debt` is the one exception: it still allows the bank's `liquidity_vault_authority` PDA to sign a CPI into an external program and move tokens even while the group/bank is paused. This breaks the invariant that "no fund movement through a bank's vault authority happens while paused," and is reachable by any permissionless caller supplying valid distributor/merkle-proof accounts — it is not merely a liquidity or availability issue, but an authorization-bypass of the pause control itself.

### Likelihood Explanation
The precondition is simply "the group or bank is paused" — a state that can be reached by normal admin governance action (e.g., during an incident) and is explicitly meant to halt further activity. No attacker privilege is required to trigger `drift_claim_bad_debt` once a valid merkle-distributor claim (amount/proof) exists for the bank's `liquidity_vault_authority`; anyone can supply the accounts and pay the transaction fee. The bypass is deterministic and repeatable every time the bank has an unclaimed Drift bad-debt allocation, independent of the pause flag.

### Recommendation
Add a `marginfi_group: AccountLoader<'info, MarginfiGroup>` account to `DriftClaimBadDebt` with the same `!group.load()?.is_protocol_paused()` constraint used in `drift_deposit`/`drift_withdraw`, and call `validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)` (or an appropriate `InstructionKind` variant) at the top of `drift_claim_bad_debt`, matching the guard pattern already established in the other Drift instructions.

### Proof of Concept
Integration test plan (Rust, using existing test harness patterns from `programs/marginfi/tests/admin_actions/actions_during_pause.rs`):
1. Set up a Drift bank and group as in existing Drift integration tests; configure a mock merkle distributor with a valid claim leaf for the bank's `liquidity_vault_authority`.
2. Pause the group via the admin `configure_group`/pause instruction (or set `bank.config.operational_state = Paused`), matching how `actions_during_pause.rs` pauses state before calling `drift_deposit`/`drift_withdraw` and asserting they return `MarginfiError::ProtocolPaused`/`BankPaused`.
3. As an unprivileged payer (no special authority), call `drift_claim_bad_debt` with a valid amount/proof.
4. Assert that the call **succeeds** (tokens move from the distributor vault into `claimant_token_account` and then to `destination_token_account`), while the parallel `drift_deposit`/`drift_withdraw` calls under the same paused state fail with `ProtocolPaused`/`BankPaused`. This asymmetry demonstrates the missing guard.

### Citations

**File:** programs/marginfi/src/instructions/drift/deposit.rs (L52-54)
```rust

        validate_asset_tags(&bank, &marginfi_account)?;
        validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)?;
```

**File:** programs/marginfi/src/instructions/drift/deposit.rs (L136-142)
```rust
pub struct DriftDeposit<'info> {
    #[account(
        constraint = (
            !group.load()?.is_protocol_paused()
        ) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,
```

**File:** programs/marginfi/src/instructions/drift/claim_bad_debt.rs (L45-63)
```rust
pub fn drift_claim_bad_debt<'info>(
    ctx: Context<'info, DriftClaimBadDebt<'info>>,
    amount: u64,
    proof: Vec<[u8; 32]>,
) -> MarginfiResult {
    ctx.accounts.create_claimant_token_account()?;
    ctx.accounts.create_destination_token_account()?;
    ctx.accounts.prefund_claim_status()?;
    let pre_claim_balance = ctx.accounts.claimant_token_balance()?;
    ctx.accounts.cpi_new_claim(amount, proof)?;
    let post_claim_balance = ctx.accounts.claimant_token_balance()?;
    let received_amount = post_claim_balance
        .checked_sub(pre_claim_balance)
        .ok_or_else(|| error!(MarginfiError::InternalLogicError))?;
    let swept_amount = ctx.accounts.cpi_transfer_to_destination()?;
    ctx.accounts
        .emit_claim_event(amount, received_amount, swept_amount)?;
    Ok(())
}
```

**File:** programs/marginfi/src/instructions/drift/claim_bad_debt.rs (L65-164)
```rust
#[derive(Accounts)]
pub struct DriftClaimBadDebt<'info> {
    /// Pays transaction fees, ATA creation, and ClaimStatus rent.
    #[account(mut)]
    pub payer: Signer<'info>,

    #[account(
        has_one = integration_acc_2 @ MarginfiError::InvalidDriftUser,
        has_one = integration_acc_3 @ MarginfiError::InvalidDriftUserStats,
        constraint = is_drift_asset_tag(bank.load()?.config.asset_tag)
            @ MarginfiError::WrongBankAssetTagForDriftOperation
    )]
    pub bank: AccountLoader<'info, Bank>,

    /// Global fee state containing the global_fee_wallet destination owner.
    #[account(
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump
    )]
    pub fee_state: AccountLoader<'info, FeeState>,

    /// The bank's liquidity vault authority. This PDA is the claimant in Drift's merkle tree.
    #[account(
        mut,
        seeds = [
            LIQUIDITY_VAULT_AUTHORITY_SEED.as_bytes(),
            bank.key().as_ref()
        ],
        bump = bank.load()?.liquidity_vault_authority_bump
    )]
    pub liquidity_vault_authority: SystemAccount<'info>,

    /// Drift user account owned by liquidity_vault_authority.
    /// CHECK: Address is locked by the bank's integration account field.
    pub integration_acc_2: UncheckedAccount<'info>,

    /// Drift user stats account owned by liquidity_vault_authority.
    /// CHECK: Address is locked by the bank's integration account field.
    pub integration_acc_3: UncheckedAccount<'info>,

    /// CHECK: MerkleDistributor account. The distributor program validates its contents during CPI.
    #[account(mut, owner = MERKLE_DISTRIBUTOR_PROGRAM_ID)]
    pub distributor: UncheckedAccount<'info>,

    /// CHECK: PDA of ["ClaimStatus", liquidity_vault_authority, distributor] under the distributor
    /// program. The distributor initializes and validates this account.
    #[account(
        mut,
        seeds = [
            b"ClaimStatus",
            liquidity_vault_authority.key().as_ref(),
            distributor.key().as_ref()
        ],
        bump,
        seeds::program = MERKLE_DISTRIBUTOR_PROGRAM_ID
    )]
    pub claim_status: UncheckedAccount<'info>,

    /// Distributor token vault.
    #[account(mut, token::mint = claim_mint)]
    pub from: Box<Account<'info, TokenAccount>>,

    pub claim_mint: Box<Account<'info, Mint>>,

    /// CHECK: Must match FeeState.global_fee_wallet. Used as the owner for the destination ATA.
    #[account(address = fee_state.load()?.global_fee_wallet @ MarginfiError::InvalidFeeAta)]
    pub global_fee_wallet: UncheckedAccount<'info>,

    /// Canonical ATA for the claim mint owned by liquidity_vault_authority.
    /// CHECK: Created idempotently and validated by address.
    #[account(
        mut,
        address = get_associated_token_address_with_program_id(
            &liquidity_vault_authority.key(),
            &claim_mint.key(),
            &token_program.key()
        ) @ MarginfiError::InvalidDriftAccount
    )]
    pub claimant_token_account: UncheckedAccount<'info>,

    /// Canonical ATA for the claim mint owned by FeeState.global_fee_wallet.
    /// CHECK: Created idempotently and validated by address.
    #[account(
        mut,
        address = get_associated_token_address_with_program_id(
            &fee_state.load()?.global_fee_wallet,
            &claim_mint.key(),
            &token_program.key()
        ) @ MarginfiError::InvalidFeeAta
    )]
    pub destination_token_account: UncheckedAccount<'info>,

    /// CHECK: validated against the Drift merkle distributor program id.
    #[account(address = MERKLE_DISTRIBUTOR_PROGRAM_ID)]
    pub merkle_distributor_program: UncheckedAccount<'info>,

    pub associated_token_program: Program<'info, AssociatedToken>,
    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}
```

**File:** programs/marginfi/src/instructions/drift/claim_bad_debt.rs (L212-271)
```rust
    fn cpi_new_claim(&self, amount: u64, proof: Vec<[u8; 32]>) -> MarginfiResult {
        let mut data = get_discrim_hash("global", "new_claim").to_vec();
        NewClaimIxArgs {
            amount_unlocked: amount,
            amount_locked: 0,
            proof,
        }
        .serialize(&mut data)?;

        let ix = Instruction {
            program_id: self.merkle_distributor_program.key(),
            accounts: vec![
                AccountMeta::new(self.distributor.key(), false),
                AccountMeta::new(self.claim_status.key(), false),
                AccountMeta::new(self.from.key(), false),
                AccountMeta::new(self.claimant_token_account.key(), false),
                AccountMeta::new(self.liquidity_vault_authority.key(), true),
                AccountMeta::new_readonly(self.token_program.key(), false),
                AccountMeta::new_readonly(self.system_program.key(), false),
            ],
            data,
        };

        let account_infos = [
            self.distributor.to_account_info(),
            self.claim_status.to_account_info(),
            self.from.to_account_info(),
            self.claimant_token_account.to_account_info(),
            self.liquidity_vault_authority.to_account_info(),
            self.token_program.to_account_info(),
            self.system_program.to_account_info(),
        ];

        let bump = self.bank.load()?.liquidity_vault_authority_bump;
        let signer_seeds: &[&[&[u8]]] =
            bank_signer!(BankVaultType::Liquidity, self.bank.key(), bump);

        invoke_signed(&ix, &account_infos, signer_seeds)?;
        Ok(())
    }

    fn cpi_transfer_to_destination(&self) -> MarginfiResult<u64> {
        let amount = accessor::amount(&self.claimant_token_account.to_account_info())?;
        if amount == 0 {
            return Ok(0);
        }

        let accounts = Transfer {
            from: self.claimant_token_account.to_account_info(),
            to: self.destination_token_account.to_account_info(),
            authority: self.liquidity_vault_authority.to_account_info(),
        };
        let bump = self.bank.load()?.liquidity_vault_authority_bump;
        let signer_seeds: &[&[&[u8]]] =
            bank_signer!(BankVaultType::Liquidity, self.bank.key(), bump);
        let cpi_ctx = CpiContext::new_with_signer(self.token_program.key(), accounts, signer_seeds);

        token::transfer(cpi_ctx, amount)?;
        Ok(amount)
    }
```

**File:** programs/marginfi/src/utils/general.rs (L266-309)
```rust
pub fn validate_bank_state(bank: &Bank, kind: InstructionKind) -> MarginfiResult {
    if bank.config.operational_state == BankOperationalState::KilledByBankruptcy {
        return err!(MarginfiError::BankKilledByBankruptcy);
    }
    // Bank exists but has not completed one-time setup (e.g. JupLend seed deposit). Block every
    // operation until init runs.
    if bank.config.operational_state == BankOperationalState::Uninitialized {
        return err!(MarginfiError::BankUninitialized);
    }

    match kind {
        InstructionKind::FailsInReduceState if bank.config.operational_state.is_reduce_only() => {
            return err!(MarginfiError::BankReduceOnly);
        }

        InstructionKind::FailsInPausedState
            if bank.config.operational_state == BankOperationalState::Paused =>
        {
            return err!(MarginfiError::BankPaused);
        }

        InstructionKind::FailsIfPausedOrReduceState
            if matches!(
                bank.config.operational_state,
                BankOperationalState::Paused
                    | BankOperationalState::ReduceOnly
                    | BankOperationalState::ReduceOnlyWithBorrowingPower
            ) =>
        {
            return match bank.config.operational_state {
                BankOperationalState::Paused => {
                    err!(MarginfiError::BankPaused)
                }
                state if state.is_reduce_only() => {
                    err!(MarginfiError::BankReduceOnly)
                }
                _ => unreachable!(),
            };
        }
        _ => {}
    }

    Ok(())
}
```
