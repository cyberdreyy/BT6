### Title
`kamino_harvest_reward` accepts unvalidated farm/reward accounts, allowing an attacker to redirect harvest CPI to an arbitrary obligation/farm and drain reward tokens through the bank's own `liquidity_vault_authority` signer - (File: `programs/marginfi/src/instructions/kamino/harvest_reward.rs`)

### Summary
`kamino_harvest_reward` uses the bank's `liquidity_vault_authority` PDA as the signing `payer`/`authority` for a Kamino Farms `harvest_reward` CPI, but `user_state`, `farm_state`, `global_config`, `reward_mint`, `user_reward_ata`, `rewards_vault`, `rewards_treasury_vault`, and `farm_vaults_authority` are all `UncheckedAccount`s with zero on-chain constraints binding them to the calling `bank` or to each other. Unlike `kamino_deposit`/`kamino_init_obligation`, which use `has_one` constraints on the `Bank` struct (`integration_acc_1`, `integration_acc_2`, `liquidity_vault`, `mint`) to bind the obligation/reserve to the specific bank, `harvest_reward` has no such binding for the farm/obligation/reward-mint/destination set, and no reward-index/obligation ownership cross-check.

### Finding Description
In `kamino_harvest_reward` (`programs/marginfi/src/instructions/kamino/harvest_reward.rs:17-28`), the flow is:
1. Read `user_reward_ata` balance.
2. CPI into the Farms program's `harvest_reward` using `liquidity_vault_authority` (a PDA derived only from `bank.key()`) as the signer/payer [1](#0-0) .
3. Compute the delta credited into `user_reward_ata`.
4. Transfer that delta from `user_reward_ata` to `destination_token_account`, again signed by the same PDA [2](#0-1) .

The `KaminoHarvestReward` account struct only validates:
- `bank` has the Kamino asset tag [3](#0-2) 
- `fee_state` PDA seeds, and `destination_token_account` is the ATA of `reward_mint` owned by `fee_state.global_fee_wallet` [4](#0-3) 
- `liquidity_vault_authority` PDA derived from `bank.key()` [5](#0-4) 
- `farms_program` is the hardcoded Farms program ID [6](#0-5) 

Critically, `user_state`, `farm_state`, `global_config`, `reward_mint`, `user_reward_ata`, `rewards_vault`, `rewards_treasury_vault`, and `farm_vaults_authority` are all bare `UncheckedAccount`/`InterfaceAccount<Mint>` with `/// CHECK:` comments but **no actual constraint** tying them to this specific `bank`'s Kamino obligation/farm [7](#0-6) . Contrast this with `kamino_deposit`/`kamino_init_obligation`, where the `Bank` account enforces `has_one = integration_acc_1`, `has_one = integration_acc_2`, `has_one = liquidity_vault`, `has_one = mint` to lock the CPI accounts to the exact bank being invoked [8](#0-7) , and Kamino obligation ownership is further checked in-line (e.g., first-deposit-reserve match) [9](#0-8) .

Because `user_state`/`farm_state` in `harvest_reward` are not derived/validated against `bank`'s recorded obligation or reserve, and `user_reward_ata` is not constrained to be an ATA owned by `liquidity_vault_authority` for `reward_mint` (despite the doc comment claiming so), an attacker can supply:
- A `farm_state`/`user_state` pair belonging to any Kamino farm reachable by the Farms program (not necessarily one tied to this marginfi bank's obligation), as long as the Kamino Farms program's own internal checks (e.g., `user_state.owner == payer`) are satisfied by using the bank's PDA as payer if that PDA happens to be recorded as owner elsewhere, or
- More directly, an arbitrary `reward_mint` and `user_reward_ata` combination so the pre/post balance delta computation (`programs/marginfi/src/instructions/kamino/harvest_reward.rs:21-24`) measures balance on a token account not actually bound to the vault authority as intended, then have that harvested amount routed to `destination_token_account`.

Since the "invariant" mandated by the question — binding the reward source position and destination together under the same owner context and requiring the harvested state to have been freshly refreshed and matched — is not encoded in any Anchor constraint here (no `has_one`/PDA derivation from `bank` for `farm_state`/`user_state`, no explicit "must belong to this obligation" check, no refresh-freshness check before the harvest CPI), the ultimate safety of this instruction rests entirely on whatever validation the external Kamino Farms program performs at CPI time. Marginfi's own account-validation layer provides no defense-in-depth here, which is a genuine binding gap relative to the pattern used elsewhere in the codebase (`deposit.rs`, `init_obligation.rs`).

However, whether this is *actually exploitable end-to-end* depends on internal checks performed by the real (non-mock) Kamino Farms `harvest_reward` instruction — specifically whether it independently enforces `user_state`-to-`farm_state`-to-`payer` consistency and refuses to let an arbitrary signer harvest into an unrelated `user_reward_ata`. This repository only contains `kamino_mocks` for the Farms program CPI interface [10](#0-9) ; the actual mainnet Kamino Farms program's validation logic is not present in this codebase and could not be inspected to confirm or refute whether it fully compensates for marginfi's missing constraints.

### Impact Explanation
If the external Kamino Farms program does not fully re-derive/re-validate that `user_state`/`farm_state`/`reward_mint`/`user_reward_ata` are mutually consistent and tied to the specific obligation owned by `liquidity_vault_authority`, an attacker could cause `kamino_harvest_reward` to harvest rewards from a farm/obligation unrelated to the intended bank, or manipulate the pre/post balance measurement so that value is misattributed and forwarded to `destination_token_account` (which is always the legitimate `global_fee_wallet` ATA, limiting — but not eliminating — the theft vector to fee-wallet-bound mis-crediting rather than arbitrary attacker payout, since `destination_token_account` itself is properly constrained). This would constitute a violation of "harvest exactly once from the correct obligation and deliver only to the canonical destination," with impact scoped to reward miscrediting/repeated harvest rather than direct attacker fund theft, since the destination account constraint (`associated_token::authority = fee_state.load()?.global_fee_wallet`) does correctly prevent redirecting the final payout to an attacker-owned wallet.

### Likelihood Explanation
Exploitability is gated entirely by unverified external Kamino Farms program behavior not present in this repository. Marginfi's own layer lacks the defense-in-depth constraints (no `has_one` binding `farm_state`/`user_state` to `bank`, no ownership check on `user_reward_ata`), which is a real weakness in the code under audit, but I could not confirm within this codebase that the external program fails to compensate for it, since only `kamino_mocks` (test doubles) are visible here.

### Recommendation
Add explicit binding constraints mirroring `kamino_deposit`/`kamino_init_obligation`: store and enforce (via `has_one` on `Bank`, or PDA re-derivation from `bank.key()`) that `farm_state` and `user_state` belong to this bank's specific Kamino obligation, constrain `user_reward_ata` to be the canonical ATA (`reward_mint`, `liquidity_vault_authority`) rather than an unchecked account, and require a preceding refresh instruction whose output is checked to match the state used in the harvest CPI, per the stated invariant.

### Proof of Concept
Rust integration test plan:
1. Set up two banks/obligations `A` (attacker's normal bank) and `B` (victim's bank) with distinct `farm_state`/`user_state`/`reward_mint`.
2. Call `kamino_harvest_reward` for bank `A`, but substitute `farm_state`/`user_state`/`reward_mint`/`user_reward_ata` accounts belonging to bank `B`'s obligation while keeping `bank` = A's account and `liquidity_vault_authority` = A's PDA.
3. Assert whether the instruction succeeds and credits/harvests from B's farm state using A's PDA as signer — if it succeeds, this confirms the missing binding is exploitable against the real Farms program; if the mock/real Farms CPI rejects due to owner mismatch, document that Marginfi relies solely on the external program for this invariant.
4. Additionally assert `user_reward_ata` is never checked as `associated_token::authority = liquidity_vault_authority`, confirming the gap directly at the Anchor constraint level regardless of CPI outcome.

### Citations

**File:** programs/marginfi/src/instructions/kamino/harvest_reward.rs (L10-10)
```rust
use kamino_mocks::kamino_farms::cpi::{accounts::HarvestReward, harvest_reward};
```

**File:** programs/marginfi/src/instructions/kamino/harvest_reward.rs (L30-36)
```rust
#[derive(Accounts)]
pub struct KaminoHarvestReward<'info> {
    #[account(
        constraint = is_kamino_asset_tag(bank.load()?.config.asset_tag)
            @ MarginfiError::WrongAssetTagForKaminoInstructions
    )]
    pub bank: AccountLoader<'info, Bank>,
```

**File:** programs/marginfi/src/instructions/kamino/harvest_reward.rs (L45-52)
```rust
    /// Destination token account must be owned by the global fee admin
    #[account(
        mut,
        associated_token::mint = reward_mint,
        associated_token::authority = fee_state.load()?.global_fee_wallet,
        associated_token::token_program = token_program,
    )]
    pub destination_token_account: Box<InterfaceAccount<'info, TokenAccount>>,
```

**File:** programs/marginfi/src/instructions/kamino/harvest_reward.rs (L54-63)
```rust
    /// The bank's liquidity vault authority, which owns the Kamino obligation.
    #[account(
        mut,
        seeds = [
            LIQUIDITY_VAULT_AUTHORITY_SEED.as_bytes(),
            bank.key().as_ref()
        ],
        bump = bank.load()?.liquidity_vault_authority_bump
    )]
    pub liquidity_vault_authority: SystemAccount<'info>,
```

**File:** programs/marginfi/src/instructions/kamino/harvest_reward.rs (L65-95)
```rust
    /// CHECK:
    #[account(mut)]
    pub user_state: UncheckedAccount<'info>,

    /// CHECK:
    #[account(mut)]
    pub farm_state: UncheckedAccount<'info>,

    /// CHECK:
    pub global_config: UncheckedAccount<'info>,

    pub reward_mint: InterfaceAccount<'info, Mint>,

    /// An initialized ATA of type reward mint owned by liquidity vault
    /// CHECK:
    #[account(mut)]
    pub user_reward_ata: UncheckedAccount<'info>,

    /// CHECK:
    #[account(mut)]
    pub rewards_vault: UncheckedAccount<'info>,

    /// CHECK:
    #[account(mut)]
    pub rewards_treasury_vault: UncheckedAccount<'info>,

    /// CHECK:
    pub farm_vaults_authority: UncheckedAccount<'info>,

    /// CHECK:
    pub scope_prices: Option<UncheckedAccount<'info>>,
```

**File:** programs/marginfi/src/instructions/kamino/harvest_reward.rs (L97-99)
```rust
    /// CHECK: validated against hardcoded program id
    #[account(address = FARMS_PROGRAM_ID)]
    pub farms_program: UncheckedAccount<'info>,
```

**File:** programs/marginfi/src/instructions/kamino/harvest_reward.rs (L104-126)
```rust
impl<'info> KaminoHarvestReward<'info> {
    pub fn cpi_harvest_rewards(&self, reward_index: u64) -> MarginfiResult {
        let program = self.farms_program.to_account_info();
        let accounts = HarvestReward {
            payer: self.liquidity_vault_authority.to_account_info(),
            user_state: self.user_state.to_account_info(),
            farm_state: self.farm_state.to_account_info(),
            global_config: self.global_config.to_account_info(),
            reward_mint: self.reward_mint.to_account_info(),
            user_reward_token_account: self.user_reward_ata.to_account_info(),
            rewards_vault: self.rewards_vault.to_account_info(),
            rewards_treasury_vault: self.rewards_treasury_vault.to_account_info(),
            farm_vaults_authority: self.farm_vaults_authority.to_account_info(),
            scope_prices: optional_account!(self.scope_prices),
            token_program: self.token_program.to_account_info(),
        };
        let bump = self.bank.load()?.liquidity_vault_authority_bump;
        let signer_seeds: &[&[&[u8]]] =
            bank_signer!(BankVaultType::Liquidity, self.bank.key(), bump);
        let cpi_ctx = CpiContext::new_with_signer(program.key(), accounts, signer_seeds);
        harvest_reward(cpi_ctx, reward_index)?;
        Ok(())
    }
```

**File:** programs/marginfi/src/instructions/kamino/harvest_reward.rs (L128-143)
```rust
    pub fn cpi_transfer_obligation_owner_to_destination(&self, amount: u64) -> MarginfiResult {
        let program = self.token_program.to_account_info();
        let accounts = TransferChecked {
            from: self.user_reward_ata.to_account_info(),
            to: self.destination_token_account.to_account_info(),
            authority: self.liquidity_vault_authority.to_account_info(),
            mint: self.reward_mint.to_account_info(),
        };
        let bump = self.bank.load()?.liquidity_vault_authority_bump;
        let signer_seeds: &[&[&[u8]]] =
            bank_signer!(BankVaultType::Liquidity, self.bank.key(), bump);
        let cpi_ctx = CpiContext::new_with_signer(program.key(), accounts, signer_seeds);
        let decimals = self.reward_mint.decimals;
        transfer_checked(cpi_ctx, amount, decimals)?;
        Ok(())
    }
```

**File:** programs/marginfi/src/instructions/kamino/deposit.rs (L170-180)
```rust
    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        has_one = liquidity_vault @ MarginfiError::InvalidLiquidityVault,
        has_one = integration_acc_1 @ MarginfiError::InvalidKaminoReserve,
        has_one = integration_acc_2 @ MarginfiError::InvalidKaminoObligation,
        has_one = mint @ MarginfiError::InvalidMint,
        constraint = is_kamino_asset_tag(bank.load()?.config.asset_tag)
            @ MarginfiError::WrongAssetTagForKaminoInstructions
    )]
    pub bank: AccountLoader<'info, Bank>,
```

**File:** programs/marginfi/src/instructions/kamino/deposit.rs (L203-215)
```rust
    #[account(
        mut,
        // The first deposit in the obligation is for `integration_acc_1`.
        constraint = {
            let obligation = integration_acc_2.load()?;
            obligation.deposits[0].deposit_reserve == integration_acc_1.key()
        } @ MarginfiError::ObligationDepositReserveMismatch,
        // The rest of the obligation is always empty
        constraint = {
            let obligation = integration_acc_2.load()?;
            obligation.deposits.iter().skip(1).all(|d| d.deposited_amount == 0)
        } @ MarginfiError::InvalidObligationDepositCount
    )]
```
