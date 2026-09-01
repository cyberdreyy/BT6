# Q5709: Request deleted at the cooldown boundary - request created by a victim

## Question
Can an unprivileged attacker delete a pending request exactly at `added_timestamp + REQUEST_COOLDOWN` to prevent it from ever executing, on a request another member created and still expects to control, breaking the invariant that retiring a request requires the same authority as creating it, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Delete a pending request exactly at `added_timestamp + REQUEST_COOLDOWN` to prevent it from ever executing, on a request another member created and still expects to control.
- Invariant to test: Retiring a request requires the same authority as creating it.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim deletion at the boundary.
