### Title
Multisig request can execute below the required confirmation threshold using stale confirmations from a removed key - (File: multisig/src/lib.rs, also multisig2/src/lib.rs)

### Summary
The bug class in the report is a "met" precondition (liquidity/threshold) that is cached and later trusted without being re-validated against the actual current state after an intervening state change. The analog in `multisig/src/lib.rs` is that `confirm()` counts a request's stored `confirmations: HashSet<PublicKey>` toward `num_confirmations` without verifying that every confirming public key is still a valid/live signer on the account. When a key is removed via `DeleteKey`, only requests *created* by that key are purged; confirmations that key left on *other people's* pending requests are never removed, so those stale confirmations still count toward reaching the K-of-N threshold. `multisig2/src/lib.rs`'s `DeleteMember`/`delete_member` has the identical gap.

### Finding Description
The custody binding that should hold is: `num_confirmations` (the configured threshold) `<= number of currently-valid signers who approved a request` before `execute_request` runs. The implementation instead enforces: `confirmations.len() + 1 >= num_confirmations`, where `confirmations` is a `HashSet<PublicKey>` collected over time and never re-validated against the live signer set at the moment of execution.

- `confirm()` in `multisig/src/lib.rs` (lines 246-266) checks only the *count* of entries in the persisted `confirmations` set for the request, then calls `execute_request` once the count crosses the threshold: [1](#0-0) 
- When a key is removed, the `DeleteKey` branch of `execute_request` only cleans up requests whose **creator** (`signer_pk`) matches the deleted key — it does not scan `self.confirmations` to strip that key from confirmation sets of *other* pending requests: [2](#0-1) 
- `assert_valid_request` (used by both `confirm` and `delete_request`) only checks that the request and confirmations map entries exist — it never checks that the confirming keys are still live access keys on the account: [3](#0-2) 

Because of this, a public key that confirmed request R1 and was later removed from the multisig (via a separate, legitimately-executed `DeleteKey` request) still counts as one of the confirmations needed to reach `num_confirmations` on R1. A minority of the *currently live* keys can therefore push R1 past the threshold and trigger `execute_request`, even though fewer than `num_confirmations` live signers actually approved it.

`multisig2/src/lib.rs`'s `delete_member` has the same structural flaw: it filters outstanding requests by `r.member == member` (the requester) to purge, but never removes `member` from `confirmations` sets of requests created by other members: [4](#0-3) 

### Impact Explanation
This breaks the core K-of-N custody guarantee of the multisig: a request (e.g., a `Transfer` of NEAR, an `AddKey`, or a `FunctionCall`) can be executed with fewer live, currently-authorized approvals than `num_confirmations` requires. This falls squarely under the Critical impact category "a multisig request executed below threshold" — funds or privileged actions (adding/removing keys, deploying code) can move or execute without the intended quorum of currently-trusted signers.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires (1) a request to be partially confirmed, (2) a subsequent, otherwise-legitimate key rotation/removal (a routine operational action — e.g. rotating out a lost/compromised device key) that occurs before R1 is fully confirmed or deleted, and (3) the remaining live keys pushing R1 to execution. Key rotation of a multisig member is a normal, expected operation (not requiring any attacker privilege beyond being one of the surviving legitimate signers), and the 15-minute `REQUEST_COOLDOWN` before a request can be deleted gives a realistic window for this race to occur unintentionally or be engineered by a subset of members.

### Recommendation
When executing `DeleteKey` (or `DeleteMember` in multisig2), iterate over all entries in `self.confirmations` (not only requests whose `signer_pk`/`member` matches the deleted key) and remove the deleted key/member from every confirmation set, decrementing effective progress accordingly. Alternatively, when counting confirmations in `confirm()`, filter the stored `confirmations` set to only those keys/members that are still currently valid signers before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig` with `num_confirmations = 3` and four active access keys A, B, C, D.
2. Key D calls `add_request_and_confirm` creating request R1 (e.g., `Transfer` of funds to an attacker-controlled account) — `confirmations(R1) = {D}`.
3. Key A calls `confirm(R1)` — `confirmations(R1) = {D, A}` (2 of 3, request not yet executed).
4. Separately, keys B, C, D confirm a legitimate `DeleteKey{public_key: A}` request (R2) and it executes, removing A's access key. Per `execute_request`'s `DeleteKey` branch, only requests where `r.signer_pk == A` are purged from `requests`/`confirmations`; R1 (created by D) is untouched, so `confirmations(R1)` still contains A's now-invalid key.
5. Key B (or C) calls `confirm(R1)`. `confirmations(R1).len() + 1 = 2 + 1 = 3 >= num_confirmations (3)`, so `execute_request(R1)` runs and transfers funds — even though only 2 live keys (D and B) plus D's original approval actually back this action; A's stale confirmation illegitimately counted toward the 3-of-N threshold. [1](#0-0) [2](#0-1)

### Citations

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```

**File:** multisig/src/lib.rs (L246-266)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig/src/lib.rs (L292-310)
```rust
    // Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert_eq!(
            env::current_account_id(),
            env::predecessor_account_id(),
            "Predecessor account must much current account"
        );
        // request must exist
        assert!(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed"
        );
        // request must have
        assert!(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests"
        );
    }
```

**File:** multisig2/src/lib.rs (L356-379)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```
