Based on my investigation, I found a genuine analog of this bug class in the marginfi `lending_account_borrow` instruction, where an oracle-price fetch error is silently swallowed via `.ok()` and the code proceeds to use a default/zero value in a rate-limiting calculation, rather than propagating the error and aborting — the same "ignore error, continue with bad data" anti-pattern as the Nibiru `bankBalance` bug.

### Title
Oracle price-fetch failures are silently swallowed and replaced with a zero price when recording borrow/withdrawal outflow for group rate-limiting - (File: `programs/marginfi/src/instructions/marginfi_account/borrow.rs`)

### Summary
In `lending_account_borrow`, the price used to value the withdrawn/borrowed amount for the group's outflow rate limiter is obtained via `fetch_unbiased_price_for_bank_with_cache(...).ok()`. Any error from the oracle price fetch (stale price, wrong oracle key, wrong number of oracle accounts, confidence exceeded, etc.) is discarded, and the code defaults `rate_limit_price` to `I80F48::ZERO` before calling `record_withdrawal_outflow`.

### Finding Description [1](#0-0) 

The code:
```
let prices =
    fetch_unbiased_price_for_bank_with_cache(&bank_pk, &bank, &clock, ctx.remaining_accounts)
        .ok();

let rate_limit_price = prices
    .as_ref()
    .map(|(adjusted, _)| adjusted.price)
    .unwrap_or(I80F48::ZERO);
let price_for_cache = prices.map(|(_, cache)| cache);
record_withdrawal_outflow(
    group_rate_limit_enabled,
    amount_pre_fee,
    amount_pre_fee,
    rate_limit_price,
    &mut bank,
    ...
)?;
```
This is structurally identical to the Nibiru `bankBalance` bug class: a fallible operation's error is discarded (`.ok()` in Rust is the analog of Go's ignored `err != nil`), and the caller proceeds to use a default/garbage value (`I80F48::ZERO`) in a downstream security-relevant computation instead of aborting the transaction. Whereas health-check price usage in `calc_weighted_asset_value_standalone` correctly propagates oracle errors via `?`/`map_err` [2](#0-1) , this rate-limit code path does not — it treats a failed oracle read the same as "no value to record," letting the borrow proceed unrestricted.

The existing `11_health_pulse` test demonstrates that even a wrong/fake oracle account is caught as an error (`WrongOracleAccountKeys`) and the corresponding price is silently reported as zero rather than the transaction failing [3](#0-2) , confirming that this "error becomes zero" behavior does occur in practice for oracle-adjacent code paths in this codebase.

### Impact Explanation
The group's rate limiter is a protocol-level defense against large sudden liquidity outflows (e.g., to slow down attacks/drains and give admins reaction time). If `record_withdrawal_outflow` accumulates value based on `amount * price`, then any borrow performed while the price oracle read fails (stale price, wrong oracle account order, wrong number of remaining accounts, confidence check failure) records an outflow **value of zero**, regardless of the actual token amount borrowed. Repeated or well-timed borrows during oracle failure conditions could bypass the rate limiter entirely, undermining a group-level financial safety control without reverting the transaction, which is a durable inconsistency with financial-safety effect (unauthorized bypass of protocol risk control).

### Likelihood Explanation
Likelihood is moderate: the health check for this ix (`check_account_init_health`) is performed *before* this price fetch and does not depend on it, so this branch's error does not block the borrow from succeeding. An attacker only needs the oracle price fetch for the borrowed bank to fail at the time of the call (e.g., a stale crank, a confidence-interval breach, or supplying oracle accounts in a way that trips one of the price-adapter checks) while other required checks still pass. Since remaining oracle accounts are attacker-supplied per-instruction, an attacker with control over which/how oracle accounts are passed for non-health-critical banks may be able to deliberately induce this failure path.

### Recommendation
Do not use `.ok()` to silently discard the price-fetch error when it feeds the rate-limiter. Either:
- Propagate the error with `?` and fail the transaction if the price cannot be fetched, forcing the caller to supply a valid, fresh oracle, or
- Skip/bypass the rate-limiter increment explicitly (with a well-reasoned justification and explicit flag) rather than implicitly recording zero value, so that a failed oracle read can never silently reduce the rate limiter's tracked outflow value.

### Proof of Concept
1. Set up a bank with the group rate limiter enabled (`group_rate_limit_enabled = true`).
2. Call `lending_account_borrow` while intentionally causing the oracle price fetch for the borrowed bank to fail — e.g., by supplying a stale oracle, wrong oracle account key(s), or wrong number of remaining oracle accounts for that bank/oracle setup, similar to the "sneaky sneaky" fake-oracle-key pattern already exercised in `11_health_pulse.spec.ts`.
3. Because the account's pre-existing collateral is sufficient to pass `check_account_init_health` independent of this fetch, the borrow succeeds.
4. Observe that `fetch_unbiased_price_for_bank_with_cache(...).ok()` returns `None`, so `rate_limit_price` becomes `I80F48::ZERO`, and `record_withdrawal_outflow` is called with a price of zero for a nonzero `amount_pre_fee`.
5. Repeat the borrow across multiple transactions under the same failure condition to confirm the group's rate-limiter accumulator does not increase despite real token outflow, demonstrating the bypass.

Note: I was unable to fully inspect the body of `record_withdrawal_outflow` (in `programs/marginfi/src/utils/general.rs`) within this session to confirm the exact accumulator formula and whether any additional safeguard exists downstream; a Devin session with full file access would be needed to verify the precise mechanics of the rate-limiter update and confirm the bypass end-to-end.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_account/borrow.rs (L222-244)
```rust
    let bank_pk = ctx.accounts.bank.key();
    let mut bank = ctx.accounts.bank.load_mut()?;
    let prices =
        fetch_unbiased_price_for_bank_with_cache(&bank_pk, &bank, &clock, ctx.remaining_accounts)
            .ok();

    let rate_limit_price = prices
        .as_ref()
        .map(|(adjusted, _)| adjusted.price)
        .unwrap_or(I80F48::ZERO);
    let price_for_cache = prices.map(|(_, cache)| cache);
    record_withdrawal_outflow(
        group_rate_limit_enabled,
        amount_pre_fee,
        amount_pre_fee,
        rate_limit_price,
        &mut bank,
        &group,
        marginfi_group_loader.key(),
        bank_pk,
        &marginfi_account,
        &clock,
    )?;
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1292-1294)
```rust
            let price_feed = price_adapter_result
                .as_ref()
                .map_err(|_| error!(MarginfiError::from(err_code)))?;
```

**File:** tests/specs/basic/11_health_pulse.spec.ts (L93-97)
```typescript
    assert.equal(cacheAfter.mrgnErr, 0);
    assert.equal(cacheAfter.internalErr, 6052); // (WrongOracleAccountKeys)
    assert.equal(cacheAfter.errIndex, 1);
    // The fake usdc price is set to zero due to the bad oracle
    assert.approximately(bytesToF64(cacheAfter.prices[1]), 0, t);
```
