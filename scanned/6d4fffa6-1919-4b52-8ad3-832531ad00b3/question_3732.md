# Q3732: Num_requests_pk underflow - dual member identity

## Question
Can an unprivileged attacker exploit the guarded decrement so the per-key request budget drifts, when one principal is registered both as `MultisigMember::Account` and as `MultisigMember::AccessKey`, breaking the invariant that the counter matches live pending requests per key, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Exploit the guarded decrement so the per-key request budget drifts, when one principal is registered both as `MultisigMember::Account` and as `MultisigMember::AccessKey`.
- Invariant to test: The counter matches live pending requests per key.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim churn and compare.
