# Q4315: Identity string collision between member kinds - receiver_id = outside

## Question
Can an unprivileged attacker make an `Account` member and an `AccessKey` member produce the same `member.to_string()` key in the `HashSet<String>` and in `num_requests_pk`, with `receiver_id` set to an account other than the multisig itself, breaking the invariant that each member maps to a unique confirmation key, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Make an `Account` member and an `AccessKey` member produce the same `member.to_string()` key in the `HashSet<String>` and in `num_requests_pk`, with `receiver_id` set to an account other than the multisig itself.
- Invariant to test: Each member maps to a unique confirmation key.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Unit test `to_string` over both variants.
