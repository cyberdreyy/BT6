### Title
Unrestricted, front-runnable `init_global_fee_state` allows an attacker to seize the protocol-wide `global_fee_admin` role - (File: `programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs`)

### Summary
`init_global_fee_state` is a "runs once per program" instruction that creates the single, program-wide `FeeState` PDA and assigns `global_fee_admin` and `global_fee_wallet` from caller-supplied arguments, with no check restricting who may call it or what values may be used.

### Finding Description
The handler `initialize_fee_state` sets `fee_state.global_fee_admin = admin_key` and `fee_state.global_fee_wallet = fee_wallet` directly from unchecked instruction arguments [1](#0-0) . The `InitFeeState` accounts struct only requires an arbitrary `Signer` as `payer` and relies on Anchor's `init` constraint on the `FEE_STATE_SEED` PDA to enforce "once per program" semantics, but does not check the payer/signer against any expected deployer or upgrade authority [2](#0-1) . Because the PDA is derived solely from the constant seed `FEE_STATE_SEED` (no group or nonce dependency), any account on the network can compute it and be the first to submit this instruction. This is structurally the same bug class as `RadiantOFT.setMinter()`: a function callable exactly once, with no restriction on the caller, and no deployment-script guarantee that the legitimate deployer calls it first — so an attacker can front-run the real `init_global_fee_state` transaction and permanently seize the privileged role for the life of the program (the PDA cannot be re-initialized).

The `global_fee_admin` role is highly privileged: it can arbitrarily change fees, the fee wallet destination, and the pause delegate admin via `edit_global_fee_state`, which only checks `has_one = global_fee_admin` on the `FeeState` account — i.e., whoever `global_fee_admin` is (attacker-controlled if the race succeeds) fully controls this [3](#0-2) . Per the documented access-control matrix, `global_fee_admin` also controls panic-pause and the global fee wallet destination for protocol-wide fees [4](#0-3) , and the `FeeState.global_fee_wallet` field documents that "All SOL fees go to this wallet" [5](#0-4) .

### Impact Explanation
If an attacker's transaction lands before the legitimate deployment's `init_global_fee_state` call, the attacker becomes the permanent `global_fee_admin` for the entire program (all groups/banks share one `FeeState` PDA). This grants the attacker the ability to: redirect all protocol-collected SOL/token fees to an attacker-controlled wallet, arbitrarily reconfigure fee rates/fixed fees and liquidation/order fee ceilings, set or clear the `pause_delegate_admin`, and panic-pause the whole protocol. Because this is a durable, unrecoverable authorization bypass with direct value-redirection potential (fee flows), it meets the bar of "concrete authorization bypass ... with financial effect."

### Likelihood Explanation
Exploitability depends entirely on the deployment process: if the legitimate deployer submits `init_global_fee_state` in the very first transaction confirmed against a freshly deployed program (e.g., atomically bundled or via a private/validator-trusted channel), the race window may be negligible in practice. However, the code itself provides no on-chain guarantee of this — there is no signer/authority check tying the call to the program's upgrade authority or any pre-agreed pubkey, and (per the bug-report analogy) "there are no deployment scripts to verify that the team will call this function right after deployment" is exactly the risk class flagged. Any public RPC/mempool exposure of the deploy sequence, or any delay between program deployment and this initialization call, creates a window for a bot to front-run it.

### Recommendation
Restrict `init_global_fee_state` (and `init_global_fee_state_v2`) to a specific known authority instead of accepting an arbitrary signer — e.g., require the payer to equal the program's upgrade authority (verified via the `ProgramData` account), or hardcode/verify `admin_key` against an expected constant similar to how `super_admin_withdraw` hardcodes `DESTINATION_WALLET` [6](#0-5) . Alternatively, initialize `FeeState` directly in the program's deployment/migration flow rather than exposing it as a standalone permissionless-callable instruction.

### Proof of Concept
1. Observe that the marginfi program has just been deployed/upgraded to a fresh program ID (or that `FeeState` PDA at `[FEE_STATE_SEED]` does not yet exist).
2. Before the legitimate team submits its `init_global_fee_state` transaction, submit your own transaction calling `init_global_fee_state` with `admin_key = <attacker_pubkey>` and `fee_wallet = <attacker_pubkey>` (or its ATA), signed by any funded keypair as `payer`.
3. Because `fee_state` uses `init` with a fixed seed `[FEE_STATE_SEED]` [7](#0-6) , this transaction, if confirmed first, permanently creates the account with the attacker as `global_fee_admin`; the legitimate team's subsequent `init_global_fee_state` call fails since the PDA already exists.
4. The attacker now calls `edit_global_fee_state` (passing themselves as `global_fee_admin`) to redirect `fee_wallet`, change fee parameters, or set `pause_delegate_admin`, exercising full control confirmed by the `has_one = global_fee_admin` check [8](#0-7) .

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L20-24)
```rust
) -> Result<()> {
    let mut fee_state = ctx.accounts.fee_state.load_init()?;
    fee_state.global_fee_admin = admin_key;
    fee_state.global_fee_wallet = fee_wallet;
    fee_state.key = ctx.accounts.fee_state.key();
```

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L37-55)
```rust
#[derive(Accounts)]
pub struct InitFeeState<'info> {
    /// Pays the init fee
    #[account(mut)]
    pub payer: Signer<'info>,

    #[account(
        init,
        seeds = [
            FEE_STATE_SEED.as_bytes()
        ],
        bump,
        payer = payer,
        space = 8 + FeeState::LEN,
    )]
    pub fee_state: AccountLoader<'info, FeeState>,

    pub system_program: Program<'info, System>,
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs (L108-121)
```rust
#[derive(Accounts)]
pub struct EditFeeState<'info> {
    /// Admin of the global FeeState
    pub global_fee_admin: Signer<'info>,

    // Note: there is just one FeeState per program, so no further check is required.
    #[account(
        mut,
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump,
        has_one = global_fee_admin @ MarginfiError::Unauthorized
    )]
    pub fee_state: AccountLoader<'info, FeeState>,
}
```

**File:** guides/ADMIN/PERMISSIONS_AND_ROLES.md (L271-274)
```markdown
| Edit global fee state | `global_fee_admin` |
| Set pause delegate admin | `global_fee_admin` |
| Panic-pause protocol | `global_fee_admin` or `pause_delegate_admin` |
| Unpause protocol (early) | `global_fee_admin` or `pause_delegate_admin` |
```

**File:** type-crate/src/types/fee_state.rs (L22-30)
```rust
/// Unique per-program. The Program Owner uses this account to administrate fees collected by the protocol
pub struct FeeState {
    /// The fee state's own key. A PDA derived from just `b"feestate"`
    pub key: Pubkey,
    /// Can modify fees, pause the protocol, etc
    pub global_fee_admin: Pubkey,
    /// The base wallet for all protocol fees. All SOL fees go to this wallet. All non-SOL fees go
    /// to the cannonical ATA of this wallet for that asset.
    pub global_fee_wallet: Pubkey,
```

**File:** programs/marginfi/src/instructions/marginfi_group/super_admin_withdraw.rs (L21-21)
```rust
const DESTINATION_WALLET: Pubkey = pubkey!("AnGdBvg8VmVHq7zyUYmC7mgjZ5pW6odwFsh6eharbzLu");
```
