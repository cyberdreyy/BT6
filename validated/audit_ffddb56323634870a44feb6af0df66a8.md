Confirmed: `delete_member` at multisig2/src/lib.rs:356-379 only purges requests where `r.member == member` (i.e., requests *originated* by the deleted member) — it does not scan other members' pending requests for stale confirmations left by the member being removed. `confirm` at line 294-315 counts confirmations purely as `confirmations.len() as u32 + 1 >= self.num_confirmations`, with no re-check that every entry in the `confirmations` set still corresponds to a `member in self.members`.

### Title
Stale confirmations from removed multisig members count toward execution threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` executes a request once `confirmations.len() + 1 >= num_confirmations`, but confirmations recorded by a member who is later deleted are never invalidated on requests they did not originate.

### Finding Description
Binding that should hold: `live_member_confirmations(request) >= num_confirmations` before `execute_request` runs. In reality, the code only enforces `stored_confirmations(request).len() + 1 >= num_confirmations`, where `stored_confirmations` can include public keys/accounts that are no longer members.

Walkthrough:
1. Member A adds a request R (via `add_request`, receiver ≠ current_account_id, e.g. a `Transfer`). `requests[R].member = A` [1](#0-0) .
2. Member B confirms R: `confirmations[R] = {B}` [2](#0-1) .
3. Governance later executes a separate, fully-confirmed `DeleteMember { member: B }` request. `delete_member` removes B from `self.members` and its access key, but only purges requests whose `r.member == B` — i.e. requests B *originated*. Request R was originated by A, not B, so R's confirmation set `{B}` is left untouched [3](#0-2) .
4. Any remaining live member C now confirms R. `confirm` computes `confirmations.len() as u32 + 1 = 2`. If `num_confirmations == 2`, R executes with only one genuinely live confirming member (C) plus the stale, now-invalid confirmation from B, who is no longer a member at all [4](#0-3) .

`assert_valid_request`, called at the start of `confirm`, only checks that the *caller* is currently a member and that the request/confirmations map entries exist — it never re-validates that previously recorded confirmers in the set are still members [5](#0-4) .

### Impact Explanation
This breaks the core K-of-N custody guarantee of the multisig: a `Transfer`, `FunctionCall`, `AddKey`/`AddMember` (i.e., full account takeover), or `DeployContract` request can be executed with fewer genuinely live, currently-authorized confirmations than `num_confirmations` requires. This matches the Critical impact category "a multisig request executed below threshold," since NEAR held by the multisig account can be moved, or a new access key/member with full control can be added, by a smaller live quorum than configured.

### Likelihood Explanation
No attacker privilege beyond normal multisig operation is required to trigger the bug's mechanics — it is a straightforward consequence of member turnover, which is an expected, routine multisig operation (removing a compromised or departing signer). Any pending request confirmed by a member prior to that member's removal becomes an exploitable stale confirmation; a remaining member (malicious or merely a confirming party unaware of the stale entry) can push such a request to execution below the intended live threshold. Likelihood is elevated in any multisig with periodic membership rotation and multiple concurrently open requests.

### Recommendation
On `delete_member`, iterate all pending requests and strip the removed member's entry from every `confirmations` set (not just requests the member originated), or alternatively re-validate on each `confirm` call that every account/key in the stored confirmation set is still present in `self.members` before counting it toward the threshold.

### Proof of Concept
1. Deploy `MultiSigContract::new(members=[A,B,C,D], num_confirmations=2)`.
2. As A: `add_request({receiver_id: multisig_account, actions:[Transfer{amount:X}]})` → request `R1`.
3. As B: `confirm(R1)` → `confirmations[R1] = {B}` (not yet executed since 1 < 2).
4. As A, D (2-of-4 confirm a separate self-request): `add_request_and_confirm({receiver_id: multisig_account, actions:[DeleteMember{member: B}]})`, then D confirms → executes `delete_member(B)`. This removes B from `self.members` and its key, but `confirmations[R1]` still equals `{B}` because R1 was created by A, not B, so it's skipped by the `r.member == member` filter in `delete_member`.
5. As C (now one of only 3 remaining live members, A/C/D): `confirm(R1)`. `confirmations.len() + 1 == 2 == num_confirmations`, so `execute_request` fires and transfers `X` — approved by only one truly live member (C) plus a stale confirmation from the now-removed B, i.e., below the intended 2-live-member threshold. [3](#0-2) [2](#0-1)

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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
    }
```
