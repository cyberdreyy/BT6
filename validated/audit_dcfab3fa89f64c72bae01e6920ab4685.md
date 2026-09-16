### Title
Front-running risk when revoking B20 `Mint`/`Seize` roles lets an about-to-be-removed role holder mint or seize funds before revocation lands - ([File: crates/common/precompiles/src/b20_asset/logic/v2.rs])

### Summary
The B20 asset/stablecoin precompile access-control model checks role membership independently on every call to a privileged operation (`mint`, `seizeWithMemo`, `pause`, etc.), with no mechanism to atomically pair a `revokeRole` transaction with disabling of a compromised or untrusted account's ability to act. Exactly like the reported Allo v2 `removePoolManager`/`withdraw` race, an untrusted `Mint`- or `Seize`-role holder who learns that a `revokeRole` transaction targeting them is pending can front-run it by submitting a higher-priority-fee `mint` or `seizeWithMemo` transaction that executes first in the same block, extracting value before their role is stripped.

### Finding Description
Role revocation is implemented as a simple storage flip with no delay, no queued-effect mechanism, and no invalidation of in-flight transactions from the account being removed: [1](#0-0) 

Both privileged operations that create or move economic value check the role at the moment of execution and otherwise proceed unconditionally:

- `mint` only requires `B20TokenRole::Mint` at call time and mints up to `supply_cap`: [2](#0-1) 

- `seize_with_memo` only requires `B20TokenRole::Seize` at call time and moves balance from any seizable account to an arbitrary destination: [3](#0-2) 

Because Base's transaction inclusion ordering within a block is governed by normal priority-fee competition (not by any special protection for pending `revokeRole` calls), any unprivileged sender who currently holds `Mint` or `Seize` can observe an admin's pending `revokeRoleCall` in the mempool and submit a competing, higher-fee `mintCall`/`seizeWithMemoCall` that lands first in the same block. This is functionally identical to the reported `RFPSimpleStrategy` issue: the admin's intent to strip privileges is defeated by the untrusted holder acting first.

### Impact Explanation
- A malicious `Mint`-role holder can mint tokens up to `supplyCap` to itself before the admin's `revokeRole` executes, diluting/inflating supply relative to backing (unbacked-supply risk for asset/stablecoin tokens whose cap represents backed collateral).
- A malicious `Seize`-role holder can drain funds from any account flagged seizable to an address of its choosing before losing the role, which is a direct theft of user funds analogous to the pool-draining scenario in the original report.
- Both are single-signed-transaction paths reachable by any account that was ever granted these operational roles, with no additional privilege required beyond normal transaction submission.

### Likelihood Explanation
This requires the attacker to already hold the `Mint` or `Seize` role (i.e., be a previously-trusted-but-now-untrusted operator, the same threat model as the original report's "untrusted pool manager"). Exploitation only requires monitoring the mempool for the admin's `revokeRoleCall` and submitting a competing transaction with a higher fee before it is revoked — a common, low-cost front-running technique requiring no special node or sequencer access.

### Recommendation
- Consider pausing the affected feature (e.g., `PausableFeature::MINT`/`SEIZE`) as part of, or immediately prior to, revoking `Mint`/`Seize` from a specific account, so in-flight privileged calls revert regardless of ordering.
- Alternatively, add a role-revocation path that atomically checks-and-blocks the specific account (e.g., a "freeze account" primitive) rather than relying purely on `revoke_role`, closing the ordering gap between "admin decides to revoke" and "revocation takes effect."
- Document that operators granting `Mint`/`Seize` roles should pause the corresponding feature before submitting `revokeRole` for an untrusted holder, since `pause`/`unpause` and role checks are both evaluated at call time (`b20_asset/logic/v2.rs:428-452`).

### Proof of Concept
1. Admin grants `Seize` role to `operator` via `grantRoleCall`.
2. Admin later decides `operator` is untrusted and submits `revokeRoleCall(role: Seize, account: operator)`.
3. `operator` observes the pending transaction in the mempool and submits `seizeWithMemoCall(from: victim, to: operator, amount: victim_balance, memo)` with a higher priority fee.
4. If `operator`'s transaction is ordered before the admin's `revokeRoleCall` in block building, `seize_with_memo` (`crates/common/precompiles/src/b20_asset/logic/v2.rs:394-420`) succeeds because the `Seize` role is still held at execution time, moving the victim's balance to `operator` before the role is stripped. [1](#0-0) [3](#0-2)

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L332-357)
```rust
    fn mint(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        to: Address,
        amount: U256,
        privileged: bool,
    ) -> Result<()> {
        B20Guards::ensure_not_paused(token, IB20::PausableFeature::MINT)?;
        if !privileged {
            B20Guards::ensure_token_role(token, caller, B20TokenRole::Mint)?;
        }
        if to == Address::ZERO {
            return Err(BasePrecompileError::revert(IB20::InvalidReceiver { receiver: to }));
        }
        B20Guards::ensure_policy_type(token, B20PolicyType::MintReceiver, to)?;
        let supply = token.accounting().total_supply()?;
        let cap = token.accounting().supply_cap()?;
        let new_supply =
            supply.checked_add(amount).ok_or_else(BasePrecompileError::under_overflow)?;
        if new_supply > cap {
            return Err(BasePrecompileError::revert(IB20::SupplyCapExceeded {
                cap,
                attempted: new_supply,
            }));
        }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L394-420)
```rust
    fn seize_with_memo(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        from: Address,
        to: Address,
        amount: U256,
        memo: B256,
    ) -> Result<()> {
        B20Guards::ensure_not_paused(token, IB20::PausableFeature::SEIZE)?;
        B20Guards::ensure_token_role(token, caller, B20TokenRole::Seize)?;
        // `to != 0` guards against a disguised burn; `from != 0` guards against a disguised mint
        // (`Transfer(0x0, to, ...)`), matching `transfer_inner`.
        if to == Address::ZERO {
            return Err(BasePrecompileError::revert(IB20::InvalidReceiver { receiver: to }));
        }
        if from == Address::ZERO {
            return Err(BasePrecompileError::revert(IB20::InvalidSender { sender: from }));
        }
        if from == to {
            return Err(BasePrecompileError::revert(IB20::InvalidReceiver { receiver: to }));
        }
        B20Guards::ensure_seizable(token, from)?;
        // Gate the destination like `mint` gates `MintReceiver`: an unset scope is always-allow, so a
        // treasury need not be allowlisted by default.
        B20Guards::ensure_policy_type(token, B20PolicyType::SeizeReceiver, to)?;
        self.move_balance(token, from, to, amount)?;
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L592-612)
```rust
    fn revoke_role(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        role: B256,
        account: Address,
        privileged: bool,
    ) -> Result<()> {
        if !privileged {
            self.ensure_role_admin_mutations_available(token, caller)?;
            let admin = token.accounting().role_admin(role)?;
            B20Guards::ensure_role(token, caller, admin)?;
        }
        if role == B20TokenRole::DefaultAdmin.id()
            && token.accounting().has_role(role, account)?
            && token.accounting().role_member_count(role)? == U256::ONE
        {
            return Err(BasePrecompileError::revert(IB20::LastAdminCannotRenounce {}));
        }
        self.revoke_role_unchecked(token, role, account, caller)
    }
```
