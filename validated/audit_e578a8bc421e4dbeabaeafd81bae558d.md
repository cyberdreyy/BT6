### Title
Missing zero/default-pubkey validation when setting vote account withdraw authority - ([File: programs/vote/src/vote_state/mod.rs])

### Summary
The vote program's `authorize` function sets a new `authorized_withdrawer` for a vote account without validating that the new authority is a usable (non-default/non-zero) `Pubkey`, mirroring the reported Derby `setDao` bug class of "no check for valid address when setting a privileged authority."

### Finding Description
In `programs/vote/src/vote_state/mod.rs`, the `authorize` function handles `VoteAuthorize::Withdrawer` by directly calling `vote_state.set_authorized_withdrawer(*authorized)` after verifying only that the *current* withdrawer signed the transaction — there is no check that `authorized` is a meaningful, non-default pubkey: [1](#0-0) 

Compare this to the `Voter` authorization path in the same function, which routes through `set_new_authorized_voter`, but the withdrawer path performs no equivalent sanity check on the supplied `authorized` key. The `authorized` value comes straight from the `VoteInstruction::Authorize` instruction data supplied by the transaction sender, so any signer of the current withdraw authority can set the new withdrawer to `Pubkey::default()` (or any other unowned/burn address) in a single transaction.

### Impact Explanation
The `authorized_withdrawer` is the sole gate for withdrawing lamports out of the vote account and for further changing the withdraw authority itself (see the `verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?` check preceding the `set_authorized_withdrawer` call). Once set to an address with no corresponding private key (e.g., the system default `Pubkey::default()`), the vote account's lamports become permanently inaccessible, and the authority can never be recovered or changed again — an irreversible, unsigned-effective loss/lock of validator funds held in the vote account, directly analogous to the "governance made impossible" impact in the reported issue.

### Likelihood Explanation
This is reachable from a single, unprivileged transaction: any account holding the current `authorized_withdrawer` signature can submit a normal `VoteInstruction::Authorize(new_pubkey, VoteAuthorize::Withdrawer)` instruction with `new_pubkey` set to an arbitrary/default value, whether by operator error or malicious front-running of a withdraw-authority key compromise. No special privileges beyond the existing valid authority are required, so likelihood of accidental misuse is non-trivial, and the effect is irreversible.

### Recommendation
Add an explicit check in `authorize` (and any analogous authorize/authorize-checked code paths) that rejects `authorized == Pubkey::default()` (or otherwise disallows setting the withdrawer to the system-owned default key) before calling `vote_state.set_authorized_withdrawer(*authorized)`, returning `InstructionError::InvalidArgument` for invalid targets.

### Proof of Concept
1. Create a vote account with withdraw authority `W`.
2. Submit a transaction invoking `VoteInstruction::Authorize(Pubkey::default(), VoteAuthorize::Withdrawer)` signed by `W`, routed through `authorize()` in `programs/vote/src/vote_state/mod.rs`.
3. The instruction succeeds because no validation rejects the default pubkey; `set_authorized_withdrawer(Pubkey::default())` is executed at [2](#0-1) .
4. All subsequent `Withdraw` instructions and further `Authorize(_, Withdrawer)` calls fail because no signer can ever produce a valid signature for `Pubkey::default()`, permanently locking the vote account's lamports.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L727-731)
```rust
        VoteAuthorize::Withdrawer => {
            // current authorized withdrawer must say "yay"
            verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;
            vote_state.set_authorized_withdrawer(*authorized);
        }
```
