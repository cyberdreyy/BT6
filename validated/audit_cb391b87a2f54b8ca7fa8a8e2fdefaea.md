### Title
Missing zero-address (`Pubkey::default()`) validation when setting `authorized_withdrawer` via `VoteInstruction::Authorize` - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The vote program's `authorize()` handler allows the current authorized withdrawer to set a new authorized withdrawer to any arbitrary pubkey — including `Pubkey::default()` (the all-zero pubkey, for which no valid private key/signature exists) — with no zero-address or validity check, permanently locking the vote account's lamports.

### Finding Description
In the `Withdrawer` arm of `authorize()`, the only check performed is that the *current* authorized withdrawer signs the transaction; the *new* authorized pubkey is accepted unconditionally and written into vote state via `vote_state.set_authorized_withdrawer(*authorized)`: [1](#0-0) 

This is the unchecked `Authorize` instruction path (as opposed to `AuthorizeChecked`, where the new authority must co-sign the transaction, which incidentally prevents this class of mistake because nothing can produce a valid signature for the zero pubkey). Because `Authorize` does not require the new authority to sign, a validator operator (or an attacker who compromises/tricks the current withdraw authority) can set `authorized_withdrawer` to `Pubkey::default()` in a single ordinary transaction. This is directly analogous to the reported bug class: a "beneficiary"/critical-authority address field accepted without a zero-address check, where the zero value is an address nobody can control, resulting in an unrecoverable configuration.

The CLI wrapper (`cli/src/vote.rs`) does add an `--allow-unsafe-authorized-withdrawer` guard, but that only rejects the withdrawer being identical to the vote account or identity pubkey — it does **not** check for `Pubkey::default()`, and more importantly this is a CLI-only guard, not enforced by the on-chain program: [2](#0-1) 
Any transaction built without going through this CLI path (e.g., directly via RPC `sendTransaction` with a hand-crafted `VoteInstruction::Authorize` instruction) bypasses this guard entirely.

### Impact Explanation
Once `authorized_withdrawer` is set to `Pubkey::default()`, the `Withdraw` instruction requires a signature from `authorized_withdrawer` to move lamports out of the vote account. Since no valid signature can ever be produced for the zero pubkey, the vote account's balance becomes permanently unwithdrawable — a direct, irreversible loss of funds for the validator operator, matching the "loss of funds via unvalidated beneficiary/authority address" bug class from the reference report. This is an unprivileged, program-level bug reachable through an ordinary signed transaction (no node/validator privilege required).

### Likelihood Explanation
Likelihood is moderate: it requires either (a) the current authorized withdrawer signer being tricked/malfunctioning into submitting an `Authorize(Pubkey::default(), Withdrawer)` instruction (e.g., via a buggy client, copy-paste error, or malicious dApp requesting a raw instruction signature), or (b) intentional self-inflicted misconfiguration. Because the on-chain program performs no sanity check and the safety net exists only in one CLI subcommand path, any other transaction-construction path (custom scripts, other wallets, direct RPC calls) is unprotected.

### Recommendation
Add validation in `authorize()` (`programs/vote/src/vote_state/mod.rs`) for the `VoteAuthorize::Withdrawer` (and ideally `Voter`) arm(s) to reject `*authorized == Pubkey::default()`, returning `InstructionError::InvalidArgument` (or similar) before calling `vote_state.set_authorized_withdrawer(*authorized)`. This enforces the invariant at the program level rather than relying solely on an opt-in CLI flag.

### Proof of Concept
1. Create a vote account and note the current `authorized_withdrawer` keypair.
2. Construct and submit a `VoteInstruction::Authorize(Pubkey::default(), VoteAuthorize::Withdrawer)` instruction signed by the current authorized withdrawer (bypassing the CLI's `vote-authorize-withdrawer` unsafe-check, e.g. by building the instruction manually or via a different client).
3. Observe the vote account's `authorized_withdrawer` field is now `Pubkey::default()`, confirmed via ` [1](#0-0) `.
4. Attempt `VoteInstruction::Withdraw` — it will always fail with `MissingRequiredSignature` because no keypair exists for the zero pubkey, permanently locking the account's lamports.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L727-731)
```rust
        VoteAuthorize::Withdrawer => {
            // current authorized withdrawer must say "yay"
            verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;
            vote_state.set_authorized_withdrawer(*authorized);
        }
```

**File:** cli/src/vote.rs (L583-598)
```rust
    if !allow_unsafe {
        if authorized_withdrawer == vote_account_pubkey.unwrap() {
            return Err(CliError::BadParameter(
                "Authorized withdrawer pubkey is identical to vote account pubkey, an unsafe \
                 configuration"
                    .to_owned(),
            ));
        }
        if authorized_withdrawer == identity_pubkey.unwrap() {
            return Err(CliError::BadParameter(
                "Authorized withdrawer pubkey is identical to identity account pubkey, an unsafe \
                 configuration"
                    .to_owned(),
            ));
        }
    }
```
