# Q5171: Nonce collision with live confirmations - single member

## Question
Can an unprivileged attacker drive `request_nonce` to an id whose confirmations still exist, on a multisig that has exactly one member, breaking the invariant that ids are never reused while stale confirmations exist, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Drive `request_nonce` to an id whose confirmations still exist, on a multisig that has exactly one member.
- Invariant to test: Ids are never reused while stale confirmations exist.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim nonce progression.
