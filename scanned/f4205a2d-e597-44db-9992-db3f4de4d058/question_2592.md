# Q2592: Request executed with the account's own key - receiver_id = self

## Question
Can an unprivileged attacker call through the account itself so the predecessor check passes trivially and the signer key is whatever the attacker installed, with `receiver_id` set to `env::current_account_id()` so `assert_self_request` passes, breaking the invariant that the predecessor check is not the only authorisation, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Call through the account itself so the predecessor check passes trivially and the signer key is whatever the attacker installed, with `receiver_id` set to `env::current_account_id()` so `assert_self_request` passes.
- Invariant to test: The predecessor check is not the only authorisation.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim a self-call with an installed key.
