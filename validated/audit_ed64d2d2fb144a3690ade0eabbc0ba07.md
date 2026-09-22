### Title
Vote program `authorize` accepts `Pubkey::default()` as new authorized withdrawer, permanently locking vote account funds - ([File: programs/vote/src/vote_state/mod.rs])

### Summary
The vote program's `authorize()` function lets the current authorized withdrawer set a new authorized withdrawer to any arbitrary pubkey — including `Pubkey::default()` (the all-zero address) — with no validation that the new authority is a plausible/spendable address.

### Finding Description
In the `VoteAuthorize::Withdrawer` branch of `authorize()`, the function only verifies that the *current* withdrawer signed the transaction, then unconditionally assigns the caller-supplied `authorized` pubkey as the new withdraw authority: [1](#0-0) 

There is no check rejecting `Pubkey::default()` (or any other pubkey with no known/derivable private key) as the new authorized withdrawer, mirroring the reported `PassThroughWalletImpl.setPassThrough()` issue where `address(0x0)` is accepted without validation. The instruction and CLI plumbing (`vote-authorize-withdrawer`, `vote-authorize-withdrawer-checked`) simply forward whatever pubkey is supplied on the transaction to this function without a zero-address guard: [2](#0-1) 

Since ed25519 signature verification can never succeed for the all-zero pubkey (no corresponding private key exists), once `authorized_withdrawer` is set to `Pubkey::default()`, subsequent `Withdraw` instructions — which require the current authorized withdrawer to sign — can never be authorized again, and `authorize()` itself also always requires the current withdrawer signature to change it again, so recovery is impossible.

### Impact Explanation
Any lamports held in the vote account (rent-exempt reserve and any additional balance sent to the vote account) become permanently unspendable/unrecoverable, i.e., a direct and irreversible loss of funds for the vote account owner. This differs from the original PassThroughWallet report's "owner mistake, but recoverable" framing — in this Agave analog the mistake is *not* recoverable, since there is no way to re-sign as `Pubkey::default()` to reset the withdrawer, making the impact strictly worse (permanent lock rather than a single-transaction misroute).

### Likelihood Explanation
This requires the current authorized withdrawer (an unprivileged signer of a submitted transaction) to submit a `VoteInstruction::Authorize(Pubkey::default(), VoteAuthorize::Withdrawer)` (or the checked variant, though that one additionally requires the new authority to sign, which is impossible for `Pubkey::default()` and would naturally fail — see caveat below). For the unchecked `Authorize` variant, no additional signature from the new authority is required, so it is trivially reachable by a simple mistake or malicious self-inflicted action from the withdrawer itself.

### Recommendation
Add an explicit check in the `VoteAuthorize::Withdrawer` (and `Voter`) arms of `authorize()`/`authorize_checked()` in `programs/vote/src/vote_state/mod.rs` to reject `Pubkey::default()` (and ideally any known-unspendable/system-reserved pubkeys) as the new authorized pubkey, returning `InstructionError::InvalidArgument` (or similar) before calling `set_authorized_withdrawer`/`set_new_authorized_voter`.

### Proof of Concept
1. Vote account `V` has `authorized_withdrawer = W`.
2. `W` (or anyone able to get `W`'s signature, e.g., via a compromised or careless key) submits a transaction with `VoteInstruction::Authorize(Pubkey::default(), VoteAuthorize::Withdrawer)` signed by `W`, per the instruction builder at `cli/src/vote.rs:222-251` and processed via the code path in `authorize()`.
3. `vote_state.set_authorized_withdrawer(Pubkey::default())` executes successfully (`programs/vote/src/vote_state/mod.rs:730`), with no rejection of the zero pubkey.
4. Any subsequent `Withdraw` instruction requires a valid signature from `Pubkey::default()`, which cannot exist, permanently locking all lamports in the vote account above the minimum required for its rent-exempt state. [3](#0-2)

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L683-731)
```rust
/// Authorize the given pubkey to withdraw or sign votes. This may be called multiple times,
/// but will implicitly withdraw authorization from the previously authorized
/// key
pub fn authorize<S: std::hash::BuildHasher, F>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    authorized: &Pubkey,
    vote_authorize: VoteAuthorize,
    signers: &HashSet<Pubkey, S>,
    clock: &Clock,
    is_vote_authorize_with_bls_enabled: bool,
    consume_pop_compute_units: F,
) -> Result<(), InstructionError>
where
    F: FnOnce() -> Result<(), InstructionError>,
{
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    match vote_authorize {
        VoteAuthorize::Voter => {
            if is_vote_authorize_with_bls_enabled && vote_state.has_bls_pubkey() {
                return Err(InstructionError::InvalidInstructionData);
            }
            let authorized_withdrawer_signer =
                verify_authorized_signer(vote_state.authorized_withdrawer(), signers).is_ok();

            vote_state.set_new_authorized_voter(
                authorized,
                clock.epoch,
                clock
                    .leader_schedule_epoch
                    .checked_add(1)
                    .ok_or(InstructionError::InvalidAccountData)?,
                None,
                |epoch_authorized_voter| {
                    // current authorized withdrawer or authorized voter must say "yay"
                    if authorized_withdrawer_signer {
                        Ok(())
                    } else {
                        verify_authorized_signer(&epoch_authorized_voter, signers)
                    }
                },
            )?;
        }
        VoteAuthorize::Withdrawer => {
            // current authorized withdrawer must say "yay"
            verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;
            vote_state.set_authorized_withdrawer(*authorized);
        }
```

**File:** cli/src/vote.rs (L222-251)
```rust
        .subcommand(
            SubCommand::with_name("vote-authorize-withdrawer")
                .about("Authorize a new withdraw signing keypair for the given vote account")
                .arg(pubkey!(
                    Arg::with_name("vote_account_pubkey")
                        .index(1)
                        .value_name("VOTE_ACCOUNT_ADDRESS")
                        .required(true),
                    "Vote account in which to set the authorized withdrawer."
                ))
                .arg(
                    Arg::with_name("authorized")
                        .index(2)
                        .value_name("AUTHORIZED_KEYPAIR")
                        .required(true)
                        .validator(is_valid_signer)
                        .help("Current authorized withdrawer."),
                )
                .arg(pubkey!(
                    Arg::with_name("new_authorized_pubkey")
                        .index(3)
                        .value_name("AUTHORIZED_PUBKEY")
                        .required(true),
                    "New authorized withdrawer."
                ))
                .offline_args()
                .nonce_args(false)
                .arg(fee_payer_arg())
                .arg(memo_arg())
                .arg(compute_unit_price_arg()),
```
