# Q5207: Confirmation keyed by signer_account_pk only - threshold equal to members

## Question
Can an unprivileged attacker confirm using a key whose presence on the account was never verified against a member set, since v1 identifies confirmers purely by `env::signer_account_pk()`, on a multisig where `num_confirmations` equals the member count, breaking the invariant that each confirmation comes from a key the account holders approved as a confirmer, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Confirm using a key whose presence on the account was never verified against a member set, since v1 identifies confirmers purely by `env::signer_account_pk()`, on a multisig where `num_confirmations` equals the member count.
- Invariant to test: Each confirmation comes from a key the account holders approved as a confirmer.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim confirmation from a newly added restricted key.
