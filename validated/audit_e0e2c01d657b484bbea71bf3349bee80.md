### Title
B20 precompile `approve()` allows front-running to double-spend allowance - (File: `crates/common/precompiles/src/b20_asset/logic/v1.rs`, `crates/common/precompiles/src/b20_asset/logic/v2.rs`, `crates/common/precompiles/src/b20_stablecoin/logic/v1.rs`, `crates/common/precompiles/src/b20_stablecoin/logic/v2.rs`)

### Summary
The B-20 precompile token standard's `approve` implementation unconditionally overwrites the `owner -> spender` allowance with the new value passed by the caller, with no compare-and-swap / expected-current-value check and no `increaseAllowance`/`decreaseAllowance` alternative exposed in the interface. This reproduces the classic ERC-20 approve race condition inside a Base system precompile that is reachable from an ordinary unprivileged L2 transaction sender.

### Finding Description
Every B-20 precompile variant (`AssetV1`, `AssetV2`, `StablecoinV1`, `StablecoinV2`) implements `approve` identically: [1](#0-0) 

The function performs zero-address checks on `caller`/`spender` and then calls `token.accounting_mut().set_allowance(caller, spender, amount)`, which blindly replaces the previous allowance value: [2](#0-1) 

`transfer_from` reads the current allowance and decrements it (except when it equals `U256::MAX`, treated as infinite): [3](#0-2) 

There is no `increaseAllowance`/`decreaseAllowance` (or "compare-and-approve") function anywhere in the `IB20` interface or its logic implementations, which would be the standard mitigation for this class of bug. This means the only way for a token owner to change a non-zero allowance to a different non-zero value is to send a new `approve` transaction that fully overwrites the old one — which is exactly the pattern vulnerable to front-running: a malicious `spender` observing the pending `approve(spender, newAmount)` transaction in the mempool can race a `transferFrom` call using the *old* allowance before the new `approve` lands, and then immediately spend the *new* allowance after it lands, extracting `oldAllowance + newAmount` instead of the owner-intended `newAmount`.

### Impact Explanation
Any spender previously granted a non-zero allowance by a token owner can, by front-running the owner's subsequent `approve` transaction with its own `transferFrom` transaction, spend up to `old_allowance + new_allowance` tokens instead of the intended `new_allowance`. This is a direct unauthorized transfer of ERC-20-equivalent B-20 asset/stablecoin funds beyond what the token owner authorized, reachable purely via unprivileged, signed L2 transactions calling the precompile's `approve`/`transferFrom` entry points — no privileged role is required by the attacker (only being a previously-approved spender).

### Likelihood Explanation
Likelihood is bounded by the precondition that the owner must be updating an already non-zero allowance for a spender who behaves maliciously (or whose keys/contract are compromised) and that the attacker can observe and outrace the pending `approve` transaction (e.g., via mempool visibility and higher-fee transaction ordering, or MEV-style transaction insertion). This is the same well-known precondition that applies to standard ERC-20 tokens; it does not require any additional protocol-level flaw, and the B-20 precompiles provide no interface-level mitigation (no increase/decrease allowance calls), so any integrator relying on `approve` with a nonzero-to-nonzero allowance change inherits this exposure.

### Recommendation
Add `increaseAllowance`/`decreaseAllowance` (or equivalent compare-and-swap `approve` variants that take an `expectedCurrentAllowance` parameter) to the `IB20` interface and corresponding precompile logic (`b20_asset` and `b20_stablecoin`, v1 and v2), and document/encourage integrators to set allowance to zero before changing it to a new non-zero value when using the raw `approve` entry point.

### Proof of Concept
1. Alice (owner) has previously called `approve(Bob, 100)` on a B-20 asset token, giving Bob a 100-token allowance, per `LOGIC.approve` in `crates/common/precompiles/src/b20_asset/logic/v1.rs:247-264`.
2. Alice decides to reduce Bob's allowance and submits `approve(Bob, 20)`.
3. Bob (malicious spender), observing the pending transaction, front-runs it with `transferFrom(Alice, Bob, 100)`, consuming the full old allowance via `transfer_from` in `crates/common/precompiles/src/b20_asset/logic/v2.rs:258-302`, which decrements the allowance to 0.
4. Alice's `approve(Bob, 20)` transaction then executes, unconditionally calling `set_allowance(Alice, Bob, 20)` and overwriting the allowance to 20, per line 260 of `logic/v1.rs`.
5. Bob immediately calls `transferFrom(Alice, Bob, 20)`, consuming the newly granted allowance as well.
6. Net result: Bob has extracted 120 tokens from Alice, even though Alice only ever intended to authorize a maximum of 100 (or reduce it to 20) at any given time — demonstrating the unauthorized-transfer impact.

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L247-264)
```rust
    fn approve(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        spender: Address,
        amount: U256,
    ) -> Result<()> {
        if caller == Address::ZERO {
            return Err(BasePrecompileError::revert(IB20::InvalidApprover { approver: caller }));
        }
        if spender == Address::ZERO {
            return Err(BasePrecompileError::revert(IB20::InvalidSpender { spender }));
        }
        token.accounting_mut().set_allowance(caller, spender, amount)?;
        token
            .accounting_mut()
            .emit_event(IB20::Approval { owner: caller, spender, amount }.encode_log_data())
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L272-301)
```rust
            .map_err(|_| BasePrecompileError::revert(IB20::InvalidSender { sender: from }))?;
        let allowance = token.accounting().allowance(from.get(), caller)?;
        let is_infinite = allowance == U256::MAX;
        if !is_infinite && allowance < amount {
            return Err(BasePrecompileError::revert(IB20::InsufficientAllowance {
                spender: caller,
                allowance,
                needed: amount,
            }));
        }
        if privileged {
            self.transfer_inner(token, from, to, amount, None)?;
        } else {
            // One SLOAD fetches all transfer policy ids, reused for the executor and
            // sender/receiver checks.
            let policies = token.accounting().transfer_policy_ids()?;
            if caller != from.get() {
                B20Guards::ensure_authorized_by_id(
                    token,
                    B20PolicyType::TransferExecutor.id(),
                    policies.executor,
                    caller,
                )?;
            }
            self.transfer_inner(token, from, to, amount, Some(&policies))?;
        }
        if is_infinite {
            return Ok(());
        }
        token.accounting_mut().set_allowance(from.get(), caller, allowance - amount)
```
