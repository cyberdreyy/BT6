### Title
Multisig request can execute below the confirmation threshold using stale confirmations from removed members - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges pending requests and confirmation sets that were *created* by the member being removed. It does not scan or scrub the `confirmations` sets of *other* pending requests that the removed member had already confirmed. Because `confirm()` counts raw confirmation-set size against `num_confirmations` without re-validating that every entry is still a current member, a request can execute having received live approval from fewer than `num_confirmations` members — the confirmation of a member removed in the interim is still counted.

### Finding Description
The multisig binding that must hold is: `confirmations recorded on a request == confirmations from accounts that are members at execution time`, and a request should only execute once `num_confirmations` *live* members have approved it.

`add_request` records a confirmation set keyed by `request_id` [1](#0-0) . `confirm()` inserts the caller's identity into that set, or executes the request once `confirmations.len() + 1 >= self.num_confirmations` [2](#0-1) . Nowhere in `confirm()` does it check whether the accounts already present in `confirmations` are still `self.members`.

`delete_member` is the only place membership changes are executed. It removes outstanding *requests* and their confirmation sets **filtered by requests whose original creator (`r.member`) equals the member being removed** — it does not touch confirmation sets of requests created by someone else that the removed member had confirmed: [3](#0-2) 

So if member `B` confirmed request `R1` (created by `A`) and is later removed via `DeleteMember{B}` (a separate request `R2`), `R1`'s confirmation set still contains `B`. `B`'s stale confirmation continues to count toward the threshold in `confirm()`, even though `B` is no longer a member and could not confirm anything ever again.

The same pattern exists in the legacy `multisig/src/lib.rs`, where `DeleteKey` only removes requests whose `signer_pk` equals the deleted key, not confirmations cast by that key on other requests: [4](#0-3) 

### Impact Explanation
This breaks the core custody/authorization binding of the multisig: "`num_confirmations` out of `N` **current** members must approve before funds move or keys change." With a stale confirmation from a removed member counted, a request (e.g. `Transfer`, `AddKey`/`AddMember` granting a new signer, `FunctionCall`) can execute with strictly fewer live-member approvals than the configured threshold. This is a multisig request executed below threshold, matching the Critical impact category ("a multisig request executed below threshold"), and can directly enable unauthorized movement of NEAR held by the multisig account.

### Likelihood Explanation
Exploitation requires no privileged access beyond being (or colluding with) an existing member who is about to be removed, or simply timing member-removal governance actions around a pending request — a realistic operational sequence for any multisig that rotates membership (e.g., removing a departing or compromised key holder) while other requests are outstanding. No foundation, owner, or victim-key compromise is required beyond the normal multisig member set itself.

### Recommendation
In `confirm()`, filter/validate `confirmations` against `self.members` before counting toward `num_confirmations` (or eagerly prune confirmations from non-members whenever membership changes). `delete_member` (and `DeleteKey` in the legacy `multisig` contract) should scan **all** confirmation sets — not just requests created by the removed member — and strip the removed member's entry from each, decrementing/adjusting recomputation accordingly before any subsequent `confirm()` call is allowed to count it.

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C]`, `num_confirmations = 2`.
2. `A` calls `add_request` to create `R1 = Transfer{ amount, receiver_id: attacker }`.
3. `B` calls `confirm(R1)` → `confirmations[R1] = {B}` (1 of 2, not yet executed).
4. `A` and `C` create and confirm `R2 = DeleteMember{ member: B }` reaching threshold 2 (both current members) → `R2` executes, removing `B` from `self.members` and revoking `B`'s access key. `delete_member` only removes requests where `r.member == B` (i.e. requests B created), so `confirmations[R1] = {B}` is untouched.
5. `A` calls `confirm(R1)`. In `confirm()`, `confirmations.len() (1, still containing removed B) + 1 (A) >= num_confirmations (2)` is true, so `execute_request` runs the `Transfer` to the attacker — despite only one still-current member (`A`) ever having approved `R1` after `B`'s removal. [2](#0-1) [3](#0-2)

### Citations

**File:** multisig2/src/lib.rs (L169-200)
```rust
    /// Add request for multisig.
    pub fn add_request(&mut self, request: MultiSigRequest) -> RequestId {
        let current_member = self.current_member().unwrap_or_else(|| {
            env::panic_str(
                "Predecessor must be a member or transaction signed with key of given account",
            )
        });
        // track how many requests this key has made
        let num_requests = self
            .num_requests_pk
            .get(&current_member.to_string())
            .unwrap_or(0)
            + 1;
        assert(
            num_requests <= self.active_requests_limit,
            "Account has too many active requests. Confirm or delete some.",
        );
        self.num_requests_pk
            .insert(&current_member.to_string(), &num_requests);
        // add the request
        let request_added = MultiSigRequestWithSigner {
            member: current_member,
            added_timestamp: env::block_timestamp(),
            request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
        self.request_nonce += 1;
        self.request_nonce - 1
    }
```

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
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
