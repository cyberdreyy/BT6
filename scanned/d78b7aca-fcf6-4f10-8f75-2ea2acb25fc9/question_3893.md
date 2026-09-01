# Q3893: Stale confirmations after key rotation - restricted key only

## Question
Can an unprivileged attacker rotate the account's keys and rely on confirmations recorded under old keys still counting, using only the function-call key `add_member` installs for `MULTISIG_METHOD_NAMES`, breaking the invariant that confirmations from removed keys never count, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Rotate the account's keys and rely on confirmations recorded under old keys still counting, using only the function-call key `add_member` installs for `MULTISIG_METHOD_NAMES`.
- Invariant to test: Confirmations from removed keys never count.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim rotation then execution.
