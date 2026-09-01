# Q1646: Execution consumed on failure - request limit reached

## Question
Can an unprivileged attacker have `remove_request` consume the request before an execution that then fails, destroying the authorisation, while the attacker's `num_requests_pk` entry sits at `active_requests_limit`, breaking the invariant that authorisation is consumed only on successful execution, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Have `remove_request` consume the request before an execution that then fails, destroying the authorisation, while the attacker's `num_requests_pk` entry sits at `active_requests_limit`.
- Invariant to test: Authorisation is consumed only on successful execution.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim a failing execution.
