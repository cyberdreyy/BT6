Based on my analysis, there's a plausible analog in marginfi's account-freeze (blacklist-equivalent) enforcement, though I was unable to fully trace the `order.rs` execution-path code that sets/clears `ACCOUNT_IN_ORDER_EXECUTION` before running out of iterations, so the exact instruction that triggers order execution while frozen is inferred from the shared authorization primitives rather than directly read line-by-line in that file.

### Title
Frozen ("blacklisted") MarginfiAccount can still be operated on via order execution because freeze checks only block the true `authority` signer, not other signers - (File: `programs/marginfi/src/state/marginfi_account.rs`)

### Summary
`MarginfiAccountSetFreeze` is marginfi's blacklist-equivalent: the docs state a frozen account's "authority is completely blocked" and "Only the group admin can perform operations on the account." [1](#0-0)  However, the actual enforcement primitives, `is_signer_authorized` and `account_not_frozen_for_authority`, only check whether the *specific signer equals the account authority*, not whether the account is frozen in an absolute sense.

### Finding Description
`is_signer_authorized` evaluates conditions in this order: receivership, then order execution, then frozen, then normal authority match: [2](#0-1) 

```
if allow_order_execution && marginfi_account.get_flag(ACCOUNT_IN_ORDER_EXECUTION) {
    return true;
}
if marginfi_account.get_flag(ACCOUNT_FROZEN) {
    return group_admin == signer;
}
```

Because the `ACCOUNT_IN_ORDER_EXECUTION` branch is checked *before* the `ACCOUNT_FROZEN` branch and unconditionally returns `true` for "any signer," a frozen account that also carries the order-execution flag would be authorized for any signer, not just the group admin. Similarly, the companion check `account_not_frozen_for_authority`, used in `LendingAccountWithdraw`, `LendingAccountRepay`, `TransferToNewAccount`, and `TransferToNewAccountPda`, only returns `false` (blocking) when **both** the account is frozen **and** the signer equals the account's own `authority`: [3](#0-2) 

```
pub fn account_not_frozen_for_authority(marginfi_account: &MarginfiAccount, signer: Pubkey) -> bool {
    !(marginfi_account.get_flag(ACCOUNT_FROZEN) && marginfi_account.authority == signer)
}
```

This mirrors the reported bug class precisely: the security gate (`notBlacklisted`/freeze) is scoped to one specific identity (`msg.sender` in the report, the account's own `authority` field here) instead of the actual protected resource's state. Any signer that is *not* the account's registered authority — e.g., a permissionless order executor/keeper acting under the `ACCOUNT_IN_ORDER_EXECUTION` flag — is not blocked by the freeze at all, exactly like the approved spender bypassing the blacklist check on the token owner.

This is used consistently across `LendingAccountWithdraw`, `LendingAccountRepay`, `TransferToNewAccount`, and `TransferToNewAccountPda`: [4](#0-3) [5](#0-4) 

### Impact Explanation
If a group admin freezes an account for compliance/investigation reasons while that account has an in-flight order (`ACCOUNT_IN_ORDER_EXECUTION` set), the intended lockout ("only group admin can act") is not actually enforced for the order-execution path: the `is_signer_authorized` short-circuit for order execution returns `true` for any signer before the frozen check is even reached, and `account_not_frozen_for_authority` does not block a non-authority signer regardless of freeze state. Funds could continue to move (withdraw/borrow/repay via order flows) on a frozen/blacklisted account, undermining the freeze's compliance/asset-recovery purpose, analogous to funds moving out of a blacklisted wallet via a pre-approved spender.

### Likelihood Explanation
Requires a specific sequence: a user places an order, the admin freezes the account (a privileged action, but the trigger condition itself doesn't require privilege from the attacker's side — the attacker only needs to have placed an order before being frozen), and the order later executes permissionlessly. Because order execution is designed to be executable by third parties (keepers), this is a realistic, unprivileged-user-reachable path once the freeze/order-timing condition lines up. I could not fully confirm from the retrieved snippets whether additional freeze checks exist specifically inside the order-execution instruction path in `order.rs` that might independently block this, which is a source of uncertainty in this assessment.

### Recommendation
Reorder the checks in `is_signer_authorized` so that `ACCOUNT_FROZEN` is evaluated before (or in conjunction with) `ACCOUNT_IN_ORDER_EXECUTION`/receivership bypasses, ensuring a frozen account cannot be acted upon by any signer other than the group admin, regardless of order-execution or other in-flight states. Likewise, `account_not_frozen_for_authority` should block all non-admin signers when `ACCOUNT_FROZEN` is set, not just the literal `authority` field.

### Proof of Concept
Conceptual (not fully verified against `order.rs` execution internals due to tool-call limits):
1. User creates a `MarginfiAccount` and places a limit order (setting `ACCOUNT_IN_ORDER_EXECUTION` when triggered/executing).
2. Group admin freezes the account via `MarginfiAccountSetFreeze` due to suspected malicious activity.
3. A keeper/any signer triggers execution of the pending order.
4. `is_signer_authorized(&account, group_admin, keeper_signer, true, true)` hits the `ACCOUNT_IN_ORDER_EXECUTION` branch and returns `true` regardless of the frozen flag, allowing the order (and associated withdraw/repay/borrow) to proceed despite the freeze.

I was unable to inspect the full `order.rs` execution instruction body within available iterations to confirm there is no additional independent frozen-check gate specifically added on top of `is_signer_authorized` for that instruction; a Devin session with full repository access would be needed to conclusively verify exploitability end-to-end.

### Citations

**File:** guides/DEVELOPERS_INTEGRATORS/ACCOUNT_LIFECYCLE.md (L65-74)
```markdown
### Frozen (Bit 6)

- **Flag**: `ACCOUNT_FROZEN` (value 64)
- **Set by**: Group admin via `MarginfiAccountSetFreeze`
- **Cleared by**: Group admin via `MarginfiAccountSetFreeze`
- **Effect**: The account's authority is completely blocked. Only the group admin can perform
  operations on the account. This is used for compliance, investigations, or protecting accounts.

A frozen account's positions continue to accrue interest and can still be liquidated if unhealthy.
The freeze only blocks the authority from interacting.
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L84-104)
```rust
pub fn is_signer_authorized(
    marginfi_account: &MarginfiAccount,
    group_admin: Pubkey,
    signer: Pubkey,
    allow_receivership: bool,
    allow_order_execution: bool,
) -> bool {
    if allow_receivership && marginfi_account.get_flag(ACCOUNT_IN_RECEIVERSHIP) {
        return marginfi_account.authority != signer; // forbidden to take receivership of your own account
    }

    if allow_order_execution && marginfi_account.get_flag(ACCOUNT_IN_ORDER_EXECUTION) {
        return true;
    }

    if marginfi_account.get_flag(ACCOUNT_FROZEN) {
        return group_admin == signer;
    }

    marginfi_account.authority == signer
}
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L106-121)
```rust
/// Checks if the account authority is allowed to act on their account based on frozen status.
///
/// Returns `true` if the action is allowed, `false` if blocked.
///
/// Returns `false` when both conditions are met:
/// - The account is frozen
/// - The signer is the account authority
///
/// This is intentionally separate from [`is_signer_authorized`] to return a distinct
/// `AccountFrozen` error in the instruction context  rather than `Unauthorized`.
pub fn account_not_frozen_for_authority(
    marginfi_account: &MarginfiAccount,
    signer: Pubkey,
) -> bool {
    !(marginfi_account.get_flag(ACCOUNT_FROZEN) && marginfi_account.authority == signer)
}
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

**File:** programs/marginfi/src/instructions/marginfi_account/repay.rs (L182-195)
```rust
    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
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
