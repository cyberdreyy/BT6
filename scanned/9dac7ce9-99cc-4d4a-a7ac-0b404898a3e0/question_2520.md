# Q2520: Threshold met by keys the attacker controls - receiver_id = self

## Question
Can an unprivileged attacker accumulate several confirming keys on the account so one principal supplies the whole threshold, with `receiver_id` set to `env::current_account_id()` so `assert_self_request` passes, breaking the invariant that distinct confirmations come from distinct principals, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Accumulate several confirming keys on the account so one principal supplies the whole threshold, with `receiver_id` set to `env::current_account_id()` so `assert_self_request` passes.
- Invariant to test: Distinct confirmations come from distinct principals.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim multi-key confirmation.
