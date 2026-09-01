# Q1721: Nonce collision with live confirmations - request limit reached

## Question
Can an unprivileged attacker drive `request_nonce` to an id whose confirmations still exist, while the attacker's `num_requests_pk` entry sits at `active_requests_limit`, breaking the invariant that ids are never reused while stale confirmations exist, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Drive `request_nonce` to an id whose confirmations still exist, while the attacker's `num_requests_pk` entry sits at `active_requests_limit`.
- Invariant to test: Ids are never reused while stale confirmations exist.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim nonce progression.
