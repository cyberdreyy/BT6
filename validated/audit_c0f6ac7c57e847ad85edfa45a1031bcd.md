## Finding: Stale confirmations from removed multisig members/keys still count toward the confirmation threshold

### Title
Multisig request can execute below the configured confirmation threshold because confirmations from removed members/keys are never purged - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
This is a direct structural analog of the external report's root cause: an action is authorized by checking a stale/incomplete state instead of the currently valid set of authorized parties. In the Lens bug, `unfollow()` checked NFT ownership instead of the actual profile owner. Here, `confirm()` checks a raw confirmation counter instead of verifying that every counted confirmation still comes from a currently live multisig member/key.

### Finding Description
`confirm()` in both `multisig/src/lib.rs` and `multisig2/src/lib.rs` decides whether to execute a request purely by comparing the size of the stored `confirmations` set to `num_confirmations`: [1](#0-0) 

When a member/key is removed (`DeleteMember` in `multisig2/src/lib.rs`, `DeleteKey` in `multisig/src/lib.rs`), the code only purges **requests that the removed member itself created**, and the `num_requests_pk`/`num_requests_per_member` counters. It never scans the `confirmations` map to strip that member's confirmations from *other* pending requests they had already confirmed: [2](#0-1) 

The v1 contract has the identical gap in the `DeleteKey` branch of `execute_request`: [3](#0-2) 

As a result, the binding `confirmations counted == confirmations by currently live members` is broken: a confirmation recorded by an account that is no longer a member still contributes to `confirmations.len()` in `confirm()`, so a request can reach `num_confirmations` with fewer actually-live confirmers than the contract's threshold requires.

### Impact Explanation
This maps to the explicitly listed Critical impact: "a multisig request executed below threshold." A `K`-of-`N` multisig's entire security guarantee is that any executed action had `K` *current* members agree. With this bug, a request only needs `K-1` (or fewer, if multiple confirmers are later removed while their stale confirmations remain) currently-live members plus previously-recorded confirmations from members who have since been removed, effectively lowering the real quorum below what governance intended. Since `MultiSigRequestAction::Transfer`, `AddKey`, `FunctionCall`, etc. can move funds or grant access, this can result in unauthorized transfers or privilege grants executed with insufficient live authorization.

### Likelihood Explanation
This does not require any external attacker or key compromise — it only requires normal multisig operation: a pending request confirmed by some members, followed by a legitimate `DeleteMember`/`DeleteKey` action removing one of those confirmers (e.g., during routine membership rotation, key rotation, or offboarding), while the earlier request remains outstanding. This is a plausible, even likely, operational sequence for any long-lived multisig account with turnover in membership or keys, making this a High/Critical-likelihood implementation defect rather than a contrived edge case.

### Recommendation
When executing `DeleteMember`/`DeleteKey`, iterate over `self.confirmations` (all pending requests, not just those authored by the removed member) and remove the departing member's/key's entry from each confirmation set. Alternatively, revalidate `confirm()` and `execute_request()` by intersecting `confirmations` with `self.members` (or the live key set) before comparing against `num_confirmations`, so stale confirmations from removed members never count toward quorum.

### Proof of Concept
Using `multisig2::MultiSigContract` initialized with `num_confirmations = 3` and members `[A, B, C, D]`:
1. `A.add_request(R)` — creates transfer request `R`, confirmations = `{}`.
2. `A.confirm(R)` — confirmations = `{A}` (1/3).
3. `B.confirm(R)` — confirmations = `{A, B}` (2/3, not yet executed).
4. Separately, members legitimately pass `DeleteMember{B}` (its own 3-of-4 confirmation flow, unrelated to `R`). `delete_member` runs: since `B` did not author `R`, `R`'s confirmations map is untouched — `{A, B}` remains stored even though `B` is no longer in `self.members`.
5. `C.confirm(R)` — `confirmations.len() + 1 == 3 == num_confirmations`, so `execute_request(R)` runs and the transfer is sent.

Only `A` and `C` are current live members who confirmed `R`; the threshold of 3 confirmations required from live members was never met, yet the request executed.

### Citations

**File:** multisig2/src/lib.rs (L294-315)
```rust
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig2/src/lib.rs (L355-379)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
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
