## #Vulnerability Found

### Title
`kamino_withdraw()` accepts collateral-token amounts and converts to liquidity via a live, unprotected exchange rate with no `min_amount_out` — ([File: programs/marginfi/src/instructions/kamino/withdraw.rs])

### Summary
The D3Vault issue is that `userWithdraw()` lets a user redeem shares (`dTokenAmount`) for underlying tokens using a live `_getExchangeRate()` computed from mutable reserve state (cash, borrows, reserves), with no `mindTokenAmount` floor, making it sandwich/slippage-exploitable. Marginfi's Kamino integration has the same shape of risk: `kamino_withdraw()` takes `amount` as a **collateral token** (share) amount, and the underlying liquidity the user actually receives is derived from Kamino's live liquidity/collateral exchange rate at CPI execution time, with no minimum-liquidity-out parameter exposed anywhere in the instruction.

### Finding Description
`kamino_withdraw` explicitly documents that the `amount` parameter is in collateral tokens, not the underlying liquidity token, and that the user must pre-compute the expected liquidity themselves using "the current exchange rate in the Kamino reserve" [1](#0-0) . The instruction burns the bank-side "shares" for the requested `amount` of collateral tokens [2](#0-1) , computes an `expected_liquidity_amount` from the reserve's *current* on-chain exchange rate right before the CPI [3](#0-2) , and then CPIs into Kamino's `withdraw_obligation_collateral_and_redeem_reserve_collateral_v2`, passing only `collateral_amount` — no `min_liquidity_amount` or similar floor [4](#0-3) . The only check afterward is `assert_within_one_token(received, expected_liquidity_amount, ...)`, which just verifies the CPI's actual output matches marginfi's own freshly-computed expectation — not that the returned liquidity meets any user-specified minimum [5](#0-4) .

Because `amount` is fixed in collateral (share) units rather than liquidity (asset) units, and the collateral→liquidity exchange rate is derived from mutable reserve state (`totalSupply / totalCollateral`, i.e., accrued liquidity vs. total collateral shares) [6](#0-5) , any exchange-rate movement between the time a user decides on a collateral `amount` and the time the withdraw executes (or is sandwiched by another transaction in the same slot manipulating reserve utilization/interest accrual) changes how much liquidity the user actually receives — with no on-chain enforcement of a minimum acceptable output. This is structurally the same class of bug as the D3Vault report: burning shares for a rate-derived asset amount with no slippage floor.

By contrast, the JupLend integration's IDL exposes a `redeem_with_min_amount_out(shares, min_amount_out)` variant specifically for this purpose [7](#0-6) , and marginfi's own JupLend withdraw path sidesteps the issue entirely by taking the withdraw `amount` in underlying-asset terms and asserting `received_underlying == token_amount` exactly [8](#0-7) . The Kamino path has no equivalent protection despite the underlying protocol's docs explicitly calling out that the conversion is rate-dependent and must be done "using the current exchange rate" by the caller [9](#0-8) .

### Impact Explanation
A user (or the marginfi program on their behalf) withdrawing a fixed collateral-token amount from a Kamino-wrapped bank can receive materially less underlying liquidity than expected if the Kamino reserve's exchange rate moves adversely between transaction construction and execution — whether from natural interest accrual timing or from a same-slot/sandwich manipulation of reserve utilization. There is no mechanism in `kamino_withdraw` to abort the transaction if the realized liquidity falls below an acceptable threshold, so value loss is silently accepted as long as it's within the internal `assert_within_one_token` self-consistency check (which only guards against CPI bugs, not economic slippage).

### Likelihood Explanation
Any user of a Kamino-wrapped bank performing a partial withdraw (non-`withdraw_all`) is exposed, since `amount` is always denominated in collateral tokens per the instruction's own documentation [10](#0-9) . No privileged access is required; this is reachable by any account holder with an active Kamino-wrapped position.

### Recommendation
Add an explicit `min_liquidity_amount_out` (or equivalent) parameter to `kamino_withdraw`, and after computing `received` from the CPI, `require!(received >= min_liquidity_amount_out, ...)` before transferring funds — mirroring the `redeem_with_min_amount_out` pattern already present in the JupLend integration's IDL. Alternatively, allow users to specify the withdraw amount in liquidity terms (as JupLend's integration does) and compute the required collateral burn internally, then verify the exact liquidity amount is delivered.

### Proof of Concept
1. User submits a `kamino_withdraw` instruction with `amount = X` collateral tokens, expecting `X * exchange_rate_now` liquidity tokens back.
2. Before the user's transaction lands (or earlier in the same slot/transaction batch), another party performs actions that shift the Kamino reserve's `totalSupply/totalCollateral` ratio unfavorably (e.g., large borrow increasing utilization/interest snapshot, or a deposit/withdraw sequence affecting `available_amount`).
3. The user's withdraw executes against the now-worse exchange rate; `expected_liquidity_amount` computed in `kamino_withdraw` [3](#0-2)  and the CPI's actual `received` both reflect the manipulated rate — self-consistent but lower than the user intended, with no revert path since there is no `min_amount_out` check anywhere in the instruction.

### Citations

**File:** programs/marginfi/src/instructions/kamino/withdraw.rs (L51-70)
```rust
/// Withdraw from a Kamino reserve through a marginfi account
///
/// # Important Note on Token Amounts:
/// The `amount` parameter is specified in terms of COLLATERAL tokens, not the underlying
/// liquidity tokens (e.g., USDC). This is important for users to understand.
///
/// Collateral tokens represent shares in the Kamino reserve. When withdrawing:
///
/// 1. The user specifies how many collateral tokens they want to withdraw.
///
/// 2. Kamino calculates the corresponding amount of liquidity tokens (e.g., USDC)
///    to return based on the current exchange rate in the Kamino reserve.
///
/// 3. If a user wants to withdraw a specific amount of liquidity tokens, they need
///    to calculate the required collateral tokens themselves using the reserve's current
///    exchange rate before making the withdrawal request.
///
/// 4. For withdrawing an entire position, use the `withdraw_all` option instead of
///    trying to calculate the exact amount.
///
```

**File:** programs/marginfi/src/instructions/kamino/withdraw.rs (L138-143)
```rust
        (collateral_amount, share_amount) = if withdraw_all {
            bank_account.withdraw_all(in_receivership)?
        } else {
            let share_amount = bank_account.withdraw(I80F48::from_num(amount))?;
            (amount, share_amount)
        };
```

**File:** programs/marginfi/src/instructions/kamino/withdraw.rs (L192-198)
```rust
    let expected_liquidity_amount = ctx
        .accounts
        .integration_acc_1
        .load()?
        .collateral_to_liquidity(collateral_amount)?;

    ctx.accounts.cpi_kamino_withdraw(collateral_amount)?;
```

**File:** programs/marginfi/src/instructions/kamino/withdraw.rs (L209-216)
```rust
    let post_transfer_vault_balance =
        accessor::amount(&ctx.accounts.liquidity_vault.to_account_info())?;
    let received = post_transfer_vault_balance - pre_transfer_vault_balance;
    assert_within_one_token(
        received,
        expected_liquidity_amount,
        MarginfiError::KaminoWithdrawFailed,
    )?;
```

**File:** programs/marginfi/src/instructions/kamino/withdraw.rs (L447-488)
```rust
    pub fn cpi_kamino_withdraw(&self, collateral_amount: u64) -> MarginfiResult {
        let withdraw_accounts = WithdrawObligationCollateralAndRedeemReserveCollateral {
            collateral_token_program: self.collateral_token_program.to_account_info(),
            instruction_sysvar_account: self.instruction_sysvar_account.to_account_info(),
            lending_market: self.lending_market.to_account_info(),
            lending_market_authority: self.lending_market_authority.to_account_info(),
            liquidity_token_program: self.liquidity_token_program.to_account_info(),
            obligation: self.integration_acc_2.to_account_info(),
            owner: self.liquidity_vault_authority.to_account_info(),
            placeholder_user_destination_collateral: None,
            reserve_collateral_mint: self.reserve_collateral_mint.to_account_info(),
            reserve_liquidity_mint: self.mint.to_account_info(),
            reserve_liquidity_supply: self.reserve_liquidity_supply.to_account_info(),
            reserve_source_collateral: self.reserve_source_collateral.to_account_info(),
            user_destination_liquidity: self.liquidity_vault.to_account_info(),
            withdraw_reserve: self.integration_acc_1.to_account_info(),
        };
        let farms_accounts = DepositFarmsAccounts {
            obligation_farm_user_state: optional_account!(self.obligation_farm_user_state),
            reserve_farm_state: optional_account!(self.reserve_farm_state),
        };
        let accounts = WithdrawObligationCollateralAndRedeemReserveCollateralV2 {
            withdraw_accounts,
            withdraw_farms_accounts: farms_accounts,
            farms_program: self.farms_program.to_account_info(),
        };
        let program = self.kamino_program.to_account_info();
        let bank_key = self.bank.key();
        let bump = self.bank.load()?.liquidity_vault_authority_bump;
        let seeds = &[
            LIQUIDITY_VAULT_AUTHORITY_SEED.as_bytes(),
            bank_key.as_ref(),
            &[bump],
        ];
        let signer_seeds: &[&[&[u8]]] = &[seeds];
        let cpi_ctx = CpiContext::new_with_signer(program.key(), accounts, signer_seeds);
        withdraw_obligation_collateral_and_redeem_reserve_collateral_v2(
            cpi_ctx,
            collateral_amount,
        )?;
        Ok(())
    }
```

**File:** tests/utils/kamino-utils.ts (L318-336)
```typescript
/**
 * The inverse of `getCollateralExchangeRate`, i.e. if you have some amount in collateral tokens and want
 * to see how many liquidity tokens it is worth.
 * @param state
 * @returns
 */
export function getLiquidityExchangeRate(state: Reserve): Decimal {
  const [totalSupply, totalCollateral] = scaledSupplies(state);

  // These should be technically impossible.
  if (totalCollateral.eq(0)) {
    return INITIAL_COLLATERAL_RATE;
  }
  if (totalSupply.eq(0)) {
    // 0 / X = 0
    return new Decimal(0);
  }
  return totalSupply.div(totalCollateral);
}
```

**File:** tests/utils/juplend/idl-types/juplend-earn.ts (L929-1032)
```typescript
    },
    {
      "name": "redeemWithMinAmountOut",
      "discriminator": [
        235,
        189,
        237,
        56,
        166,
        180,
        184,
        149
      ],
      "accounts": [
        {
          "name": "signer",
          "writable": true,
          "signer": true
        },
        {
          "name": "ownerTokenAccount",
          "writable": true
        },
        {
          "name": "recipientTokenAccount",
          "writable": true
        },
        {
          "name": "lendingAdmin"
        },
        {
          "name": "lending",
          "writable": true
        },
        {
          "name": "mint",
          "relations": [
            "lending",
            "rewardsRateModel"
          ]
        },
        {
          "name": "fTokenMint",
          "writable": true,
          "relations": [
            "lending"
          ]
        },
        {
          "name": "supplyTokenReservesLiquidity",
          "writable": true
        },
        {
          "name": "lendingSupplyPositionOnLiquidity",
          "writable": true
        },
        {
          "name": "rateModel"
        },
        {
          "name": "vault",
          "writable": true
        },
        {
          "name": "claimAccount",
          "writable": true,
          "optional": true
        },
        {
          "name": "liquidity",
          "writable": true
        },
        {
          "name": "liquidityProgram",
          "writable": true,
          "relations": [
            "lendingAdmin"
          ]
        },
        {
          "name": "rewardsRateModel"
        },
        {
          "name": "tokenProgram"
        },
        {
          "name": "associatedTokenProgram",
          "address": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
        },
        {
          "name": "systemProgram",
          "address": "11111111111111111111111111111111"
        }
      ],
      "args": [
        {
          "name": "shares",
          "type": "u64"
        },
        {
          "name": "minAmountOut",
          "type": "u64"
        }
      ]
```

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L230-246)
```rust
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
```

**File:** guides/DEVELOPERS_INTEGRATORS/KAMINO_INTEGRATION.md (L112-124)
```markdown
## Token Amount Types by Instruction

| Instruction | Token Amount Type | Notes |
|-------------|------------------|-------|
| Deposit | Liquidity token amount | Raw underlying token (e.g., USDC, SOL) |
| Withdraw | Collateral token amount | Must convert from collateral to liquidity token amount |
| Liquidate | Collateral token amount | Must convert from collateral to liquidity token amount |

**Important:** Deposit operations accept liquidity token amounts (the underlying asset), while
withdraw and liquidate operations work with collateral token amounts. Since collateral tokens
appreciate in value relative to the liquidity token as interest accumulates, liquidators and
withdrawers must manually convert from collateral token amounts to liquidity token amounts using the
current exchange rate.
```

**File:** programs/marginfi/src/lib.rs (L852-857)
```rust
    /// (user) Withdraw from a Kamino pool through a marginfi account
    /// * amount - in the collateral token (NOT liquidity token), in native decimals. Must convert
    ///     from collateral to liquidity token amounts using the current exchange rate.
    /// * if group rate limits are enabled, include the withdrawn bank's oracle group in
    ///   `remaining_accounts`
    /// * flags - optional bitflags:
```
