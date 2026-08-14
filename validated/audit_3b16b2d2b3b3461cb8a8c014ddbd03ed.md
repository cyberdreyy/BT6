## Analysis

The External Report's bug class is: **an instruction CPIs into an external protocol assuming exact, zero-slippage equality between a locally pre-computed value and the CPI's actual on-chain result, with no tolerance/fallback — any deviation permanently reverts the instruction.**

The strongest reachable analog in marginfi-v2 is the `juplend_deposit` / `juplend_withdraw` instructions, which enforce **exact-equality (`require_eq!`)** between a locally mirrored math formula and the actual result of a CPI into the external, unprivileged JupLend program — unlike the equivalent Kamino integration, which explicitly tolerates a 1-token rounding delta (`assert_within_one_token`).

### Title
JupLend deposit/withdraw enforce zero-tolerance exact-match against an external CPI's actual result, permanently DoS-ing user deposits/withdrawals on any legitimate protocol-side deviation - ([File: programs/marginfi/src/instructions/juplend/deposit.rs])

### Summary
`juplend_deposit` and `juplend_withdraw` pre-compute an "expected" fToken share amount using a locally mirrored copy of JupLend's math (`expected_shares_for_deposit_from_rates` / `expected_shares_for_withdraw_from_rate` / `expected_assets_for_redeem_from_rate`), then CPI into the real, externally-controlled JupLend program, and finally assert the CPI's actual on-chain result **exactly** matches the local prediction via `require_eq!`. There is no slippage tolerance whatsoever on this check — any legitimate deviation between marginfi's mirrored formula and JupLend's real on-chain computation causes the instruction to revert, permanently DoS-ing that user-facing operation, exactly as in the `createPair()` zero-slippage report where `addLiquidity` was called with `amountDesired == amountMin`.

### Finding Description
In `juplend_deposit` [1](#0-0) , the expected minted shares are computed from `lending.liquidity_exchange_price` / `lending.token_exchange_price` immediately after refreshing rates via `cpi_update_rate()`, and then compared against the actual post-CPI fToken balance delta with:
```
require_eq!(minted_shares, expected_shares, MarginfiError::JuplendDepositFailed);
```
Similarly, `juplend_withdraw` enforces two independent exact-match checks after the CPI [2](#0-1) :
```
require_eq!(received_underlying, token_amount, MarginfiError::JuplendWithdrawFailed);
...
require_eq!(burned_shares, shares_to_burn, MarginfiError::JuplendWithdrawFailed);
```
Both instructions rely on marginfi's own reimplementation of JupLend's internal share/asset conversion math (`juplend-mocks/src/state.rs`) exactly reproducing the real, externally-controlled JupLend program's behavior, including its two-step, intermediate-rounding conversion, which the code's own documentation acknowledges can differ from a naive single-step formula: "The intermediate floor divisions can cause up to 1 unit of rounding loss vs the naive single-step formula when exchange prices != 1e12" [3](#0-2) .

By contrast, the analogous Kamino integration explicitly tolerates rounding deltas rather than requiring bit-exact equality: `assert_within_one_token(received, expected_liquidity_amount, MarginfiError::KaminoWithdrawFailed)` [4](#0-3) . The JupLend path has no such allowance — it is architecturally a "zero slippage" assumption against a third-party, unprivileged, externally upgradeable protocol whose exact rounding/fee/reward-accrual behavior is not under marginfi's control, exactly mirroring the `createPair()` finding's root cause (CPI with `amountDesired == amountMin` / no slippage parameter against code marginfi does not control).

### Impact Explanation
If JupLend's real deployed program ever legitimately produces a share/asset amount that differs from marginfi's mirrored formula by even one unit — due to a JupLend upgrade, an additional fee/rebate step, reward-rate-model interaction, or any rounding-order difference not perfectly replicated in `juplend-mocks/src/state.rs` — every `juplend_deposit` and `juplend_withdraw` call will permanently revert. This is a durable denial-of-service on all user deposit/withdraw functionality for JupLend banks, since there is no fallback, tolerance, or slippage parameter to absorb the discrepancy, and the check cannot be satisfied by any unprivileged user action.

### Likelihood Explanation
The mirrored math is presently believed to match JupLend's implementation exactly (as documented and unit-tested), so under current conditions the check passes deterministically within a single atomic transaction. However, this is inherently fragile: it depends on marginfi's mirrored formula staying perfectly synchronized with an externally-controlled, upgradeable third-party program (JupLend) forever, with zero tolerance for drift — the same class of unaudited-assumption risk flagged in the original report, where a change in the counterparty's behavior (not marginfi's own contract) silently breaks a core user path.

### Recommendation
Introduce a tolerance band (as already done for Kamino via `assert_within_one_token`) instead of `require_eq!` zero-tolerance equality for the JupLend deposit/withdraw share/asset checks, or add an explicit slippage/tolerance parameter so legitimate small deviations from the mirrored formula do not permanently brick deposits/withdrawals for a bank.

### Proof of Concept
Not directly reproducible against current test fixtures (the mocked JupLend program is intentionally kept in lockstep with the mirrored formula), but the failure mode is structurally guaranteed by design: any future JupLend mainnet upgrade, or any edge case not captured in `expected_shares_for_deposit_from_rates`/`expected_shares_for_withdraw_from_rate`/`expected_assets_for_redeem_from_rate`, produces a fToken/asset delta unequal to the locally computed value, tripping `require_eq!` and reverting the transaction — with no way for users or admins to work around it short of a program upgrade.

### Citations

**File:** programs/marginfi/src/instructions/juplend/deposit.rs (L55-82)
```rust
    let expected_shares = {
        let lending = ctx.accounts.integration_acc_1.load()?;
        // Compute expected shares minted (round-down) using the same math as JupLend.
        expected_shares_for_deposit_from_rates(
            amount,
            lending.liquidity_exchange_price,
            lending.token_exchange_price,
        )
        .ok_or_else(|| error!(MarginfiError::MathError))?
    };

    let pre_f_token_balance = accessor::amount(&ctx.accounts.integration_acc_2.to_account_info())?;

    // Move underlying into the vault and deposit into JupLend.
    ctx.accounts.cpi_transfer_user_to_liquidity_vault(amount)?;
    ctx.accounts.cpi_juplend_deposit(amount, authority_bump)?;

    let post_f_token_balance = accessor::amount(&ctx.accounts.integration_acc_2.to_account_info())?;
    let minted_shares = post_f_token_balance
        .checked_sub(pre_f_token_balance)
        .ok_or_else(|| error!(MarginfiError::MathError))?;

    // Exact match required.
    require_eq!(
        minted_shares,
        expected_shares,
        MarginfiError::JuplendDepositFailed
    );
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

**File:** programs/juplend-mocks/src/state.rs (L134-148)
```rust
    /// Expected fToken shares minted when depositing `assets` underlying.
    ///
    /// Mirrors JupLend's actual deposit flow: **round down** via the liquidity layer.
    ///
    /// The deposit goes through a two-step conversion in the liquidity layer before
    /// computing shares. The intermediate floor divisions can cause up to 1 unit of
    /// rounding loss vs the naive single-step formula when exchange prices != 1e12.
    ///
    /// Formula (1e12 precision):
    /// ```text
    /// raw   = floor(assets * 1e12 / liquidity_exchange_price)
    /// norm  = floor(raw * liquidity_exchange_price / 1e12)
    /// shares = floor(norm * 1e12 / token_exchange_price)
    /// ```
    /// https://github.com/Instadapp/fluid-solana-programs/blob/830458299be42eaeb6e1fe8fef6aa23444430a10/programs/lending/src/utils/deposit.rs#L68-L86
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
