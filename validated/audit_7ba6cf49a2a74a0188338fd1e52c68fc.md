## Finding

### Title
Front-runnable before/after balance diff permanently strands Kamino farm reward tokens - (File: `programs/marginfi/src/instructions/kamino/harvest_reward.rs`)

### Summary
`kamino_harvest_reward` computes the amount of reward tokens to sweep to the global fee wallet by taking a before/after balance snapshot around the CPI to Kamino Farms' `harvest_reward`, exactly the same anti-pattern flagged in Sherlock H-7 for `VaultRewarderLib._claimVaultRewards`. If the reward tokens land in the intermediary account before marginfi's instruction executes, the computed delta is reduced or zeroed, and the actual tokens become permanently stuck.

### Finding Description
The instruction is documented and implemented as permissionless: [1](#0-0) 

Its implementation snapshots the balance of `user_reward_ata` (an ATA deterministically derived from `liquidity_vault_authority` + `reward_mint`, i.e. a fully public, predictable address) before and after CPI-ing into Kamino Farms, then transfers only the delta to the destination: [2](#0-1) 

This mirrors the vulnerable pattern in the external report exactly:
```
balanceAfter - balancesBefore[i]
```
which "will always produce zero if the call... is front-run."

The `user_reward_ata` is a normal SPL token account, and the underlying Kamino Farms `harvest_reward` instruction is farm-configurable to be permissionless via `is_harvesting_permissionless` (`updateIsHarvestingPermissionless` config option; enforced by Kamino error `HarvestingNotPermissionlessPayerMismatch`): [3](#0-2) [4](#0-3) 

For any Kamino Farm where the admin has enabled `is_harvesting_permissionless` (a common setting to let keeper bots auto-harvest on behalf of stakers), any external party can directly invoke Kamino Farms' `harvest_reward` instruction, targeting `user_state` = the bank's obligation user state (public) and `user_reward_token_account` = the deterministic `user_reward_ata` owned by `liquidity_vault_authority`. This lands the real reward tokens in that ATA before marginfi's own `kamino_harvest_reward` instruction runs.

When marginfi's instruction subsequently executes:
- `pre_transfer_balance` is read after the front-run has already deposited the reward.
- The inner CPI call to Kamino's `harvest_reward` yields nothing further (reward already claimed for that period).
- `received = post_transfer_balance - pre_transfer_balance` == 0.
- `cpi_transfer_obligation_owner_to_destination(0)` sweeps nothing.

The already-claimed reward tokens remain in `user_reward_ata`, which is only ever swept by this same delta-based instruction — there is no other sweep path for that ATA (unlike the Drift analog `DriftHarvestReward::cpi_transfer_to_destination`, which transfers the entire live balance rather than a diff, and is therefore not affected by this class of bug): [5](#0-4) 

### Impact Explanation
Reward tokens intended for the global fee wallet (and ultimately for redistribution to depositors/protocol) can be permanently stranded in a PDA-owned ATA with no recovery instruction. Every front-run of `kamino_harvest_reward` nets out to a zero-value sweep while the actual rewards sit unclaimed and unswept forever, matching the "loss of assets… no ability to rescue stuck tokens" reasoning that drove the Sherlock escalation to High.

### Likelihood Explanation
Exploitability is gated on the specific Kamino Farm attached to a bank having `is_harvesting_permissionless` enabled on the Kamino side (a farm-admin-controlled flag external to marginfi). Where that flag is set — a realistic and common configuration to support keeper-driven harvesting — the attack is free, repeatable, and requires no special privileges, only observing/predicting that a `kamino_harvest_reward` call is about to land and beating it with a direct call to Kamino Farms.

### Recommendation
Do not rely on a before/after balance diff to determine the sweep amount. Instead, sweep the entire current balance of `user_reward_ata` to the destination after the CPI (as already done in `DriftHarvestReward::cpi_transfer_to_destination`), so that any pre-existing or front-run balance is captured rather than stranded.

### Proof of Concept
1. Bank `B` has a Kamino integration; its `liquidity_vault_authority` PDA is the owner of `user_state` in the attached Kamino Farm, and the farm has `is_harvesting_permissionless = true`.
2. Attacker computes the deterministic `user_reward_ata` = ATA(`liquidity_vault_authority`, `reward_mint`) and, if needed, creates it (idempotent/permissionless).
3. Attacker directly calls Kamino Farms' `harvest_reward` instruction with `payer` = themselves, `user_state` = `B`'s obligation user state, `user_reward_token_account` = `user_reward_ata`, harvesting the pending reward into that ATA.
4. Anyone subsequently calls marginfi's `kamino_harvest_reward` for bank `B`; `pre_transfer_balance` already equals the just-harvested amount, the inner CPI harvest yields nothing new, `received` computes to `0`, and the transfer to the fee wallet moves nothing.
5. The reward tokens remain in `user_reward_ata` indefinitely, since no other marginfi instruction sweeps that account's full balance.

### Citations

**File:** programs/marginfi/src/lib.rs (L878-887)
```rust
    /// (permissionless) Harvest the specified reward index from the Kamino Farm attached to this
    /// bank. Rewards are always sent to the global fee wallet's canonical ATA.
    ///
    /// * `reward_index` — index of the reward token in the Kamino Farm's reward list
    pub fn kamino_harvest_reward(
        ctx: Context<KaminoHarvestReward>,
        reward_index: u64,
    ) -> MarginfiResult {
        kamino::kamino_harvest_reward(ctx, reward_index)
    }
```

**File:** programs/marginfi/src/instructions/kamino/harvest_reward.rs (L17-28)
```rust
pub fn kamino_harvest_reward(
    ctx: Context<KaminoHarvestReward>,
    reward_index: u64,
) -> MarginfiResult {
    let pre_transfer_balance = accessor::amount(&ctx.accounts.user_reward_ata.to_account_info())?;
    ctx.accounts.cpi_harvest_rewards(reward_index)?;
    let post_transfer_balance = accessor::amount(&ctx.accounts.user_reward_ata.to_account_info())?;
    let received = post_transfer_balance - pre_transfer_balance;
    ctx.accounts
        .cpi_transfer_obligation_owner_to_destination(received)?;
    Ok(())
}
```

**File:** idls-complete/kamino_farms.json (L687-691)
```json
    {
      "code": 6066,
      "name": "HarvestingNotPermissionlessPayerMismatch",
      "msg": "Harvesting is not permissionless, payer does not match user state owner"
    },
```

**File:** tests/fixtures/kamino_farms.ts (L1576-1582)
```typescript
          {
            "name": "updateDelegatedAuthority"
          },
          {
            "name": "updateIsHarvestingPermissionless"
          }
        ]
```

**File:** programs/marginfi/src/instructions/drift/harvest_reward.rs (L169-188)
```rust
    pub fn cpi_transfer_to_destination(&self) -> MarginfiResult {
        let program = self.token_program.to_account_info();
        let accounts = TransferChecked {
            from: self.intermediary_token_account.to_account_info(),
            to: self.destination_token_account.to_account_info(),
            authority: self.liquidity_vault_authority.to_account_info(),
            mint: self.reward_mint.to_account_info(),
        };

        let bump = self.bank.load()?.liquidity_vault_authority_bump;
        let signer_seeds: &[&[&[u8]]] =
            bank_signer!(BankVaultType::Liquidity, self.bank.key(), bump);
        let cpi_ctx = CpiContext::new_with_signer(program.key(), accounts, signer_seeds);

        let decimals = self.reward_mint.decimals;
        // Transfer entire balance
        let amount = accessor::amount(&self.intermediary_token_account.to_account_info())?;
        transfer_checked(cpi_ctx, amount, decimals)?;
        Ok(())
    }
```
