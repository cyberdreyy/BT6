# Q2021: Add_full_access_key through a request - nonce driven high

## Question
Can an unprivileged attacker execute an `AddKey` with no permission against the account so a full access key removes the multisig entirely, after `request_nonce` was driven toward `u32::MAX` by repeated `add_request` calls, breaking the invariant that installing a full access key needs the full threshold, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Execute an `AddKey` with no permission against the account so a full access key removes the multisig entirely, after `request_nonce` was driven toward `u32::MAX` by repeated `add_request` calls.
- Invariant to test: Installing a full access key needs the full threshold.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim the request and enumerate keys.
