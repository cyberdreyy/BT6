# Q0296: Num_requests_pk underflow - cooldown boundary

## Question
Can an unprivileged attacker exploit the guarded decrement so the per-key request budget drifts, at exactly `added_timestamp + REQUEST_COOLDOWN`, breaking the invariant that the counter matches live pending requests per key, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Exploit the guarded decrement so the per-key request budget drifts, at exactly `added_timestamp + REQUEST_COOLDOWN`.
- Invariant to test: The counter matches live pending requests per key.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim churn and compare.
