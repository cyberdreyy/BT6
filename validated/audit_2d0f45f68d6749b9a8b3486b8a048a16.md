### Title
Stale confirmations from removed multisig members count toward the confirmation threshold - (File: `multisig2/src/lib.rs`)

### Summary
`DeleteMember`/`DeleteKey` removes a member from the live member set but only purges confirmations for requests that the removed member itself *originated*. Confirmations the removed member cast on requests **originated by other members** are never cleared. Since `confirm()` only compares the raw size of the stored confirmation set against `num_confirmations`, without checking that every signer in that set is still a current member, a request can later be executed by counting a confirmation that came from an account/key that is no longer part of the multisig — i.e. it can execute with fewer *live* confirmations than the configured threshold.

### Finding Description
`confirm()` in `multisig2/src/lib.rs` decides whether to execute a request purely from the cardinality of the stored `confirmations: HashSet<String>` for that request: [1](#0-0) 

Member removal is implemented in `delete_member`, which only cleans up requests that the *removed member itself* added (`r.member == member`), and never scans other requests' `confirmations` sets for entries belonging to the removed member: [2](#0-1) 

The intended custody binding is: **number of confirmations recorded for a request == number of currently-live members who confirmed it**. Because stale entries in `confirmations` are never invalidated when a member is deleted, this binding breaks — a departed member's historical confirmation is silently counted as if it still represented a live, authorized signer.

The identical pattern exists in the older `multisig/src/lib.rs` contract: the `DeleteKey` action only removes requests where `r.signer_pk == pk` (i.e., requests originated by that key) and clears `num_requests_pk`, but does not scan `confirmations` for other pending requests that the deleted key had already confirmed: [3](#0-2) [4](#0-3) 

### Impact Explanation
This lets a request be executed (including `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, etc.) with fewer *currently authorized* confirmations than `num_confirmations` requires, because one or more of the counted confirmations belongs to an account/key that has since been removed from the multisig. This is a direct instance of "a multisig request executed below threshold," which the custody-binding rules classify as Critical impact — funds can move, or membership/config can change, without the intended quorum of live signers.

### Likelihood Explanation
This requires no external attacker and no compromise of any key: it can occur purely from the multisig's own normal operational sequence — (1) a pending request is confirmed by member X, (2) member X is later removed via a legitimate `DeleteMember`/`DeleteKey` request (e.g., off-boarding, key rotation, compromise response), (3) the original request is still pending and gets its final confirmation from a different member, silently including X's stale confirmation in the count. Any multisig that rotates or removes members while unrelated requests are outstanding is exposed; no special privilege beyond ordinary multisig membership actions is needed to trigger it, and the remaining members reaching the numeric threshold need not realize a departed member's vote is still baked in.

### Recommendation
When removing a member (`delete_member`/`DeleteKey` action), iterate over **all** outstanding requests' confirmation sets (not just those the member originated) and strip the removed member's entry from each. Alternatively, validate at `confirm()`/execution time that every entry in the confirmation set still corresponds to a current member of `self.members`, discounting stale entries before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members: [A, B, C, D], num_confirmations: 3)`.
2. `A` calls `add_request(R)` (e.g., `Transfer` to an attacker-controlled account) — confirmations for `R` = `{}`.
3. `B` calls `confirm(R)` → confirmations for `R` = `{B}` (1 < 3, not executed).
4. `C` calls `confirm(R)` → confirmations for `R` = `{B, C}` (2 < 3, not executed).
5. Members execute a separate, properly-confirmed `DeleteMember { member: C }` request (a routine off-boarding action) — per `delete_member`, only requests where `r.member == C` are purged; `R` was originated by `A`, so it is untouched and its confirmation set still contains `C`.
6. `D` calls `confirm(R)` → `confirmations.len() (=2) + 1 >= num_confirmations (3)` → `execute_request(R)` runs.
7. Result: `R` executes with confirmations from `{B, C, D}`, but `C` was removed from the multisig before execution — only 2 live members (`B`, `D`) plus the originator `A`'s intent, and a stale, no-longer-authorized confirmation from `C`, satisfied a nominal 3-of-4 threshold that was never actually met by 3 *live* signers at execution time.

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

**File:** multisig/src/lib.rs (L248-266)
```rust
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
