### Title
Stale confirmations from removed multisig members can be counted toward the confirmation threshold, allowing requests to execute below the configured number of live signers - (File: `multisig2/src/lib.rs`, also present in `multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm` counts every entry already stored in a request's `confirmations` set toward `num_confirmations`, without verifying that the accounts/keys that produced those entries are still members of the multisig. `delete_member` (and, in `multisig`, the `DeleteKey` action) only purges confirmations for requests that were *originated* by the removed member, not confirmations that the removed member cast on requests originated by someone else. As a result, a request can be executed using fewer live-member confirmations than `num_confirmations` requires.

### Finding Description
The multisig's core invariant should be: `confirmations counted == confirmations from accounts that are still current members`. This is broken because of an incomplete cleanup path.

- `add_request` creates a request with an empty `confirmations: HashSet`. [1](#0-0) 

- `confirm` inserts the calling member's identity into that set, and once `confirmations.len() + 1 >= num_confirmations`, it removes and executes the request: [2](#0-1) 

- `delete_member` removes a member and cleans up confirmations only for requests whose **originating signer** (`r.member`) equals the member being deleted: [3](#0-2) 

This filter (`r.member == member`) checks only the request creator, not the set of accounts that previously called `confirm` on other, unrelated requests. If a now-removed member had confirmed (but not created) some other pending request, that stale confirmation entry is never removed from `self.confirmations`. It sits in storage and is still counted the next time `confirm` is called by a current member on that same request, inflating the apparent confirmation count with an entry belonging to an account that is no longer a member.

The identical structural bug exists in `multisig/src/lib.rs`'s `DeleteKey` handling, which also only clears confirmations/requests where `r.signer_pk == pk` (the original requester), not confirmations cast by that key on other requests: [4](#0-3) 

This is directly analogous to the reported bug class: an actor's participation is supposed to be revoked (lender opts out / member is removed), but a piece of state tied to that actor's prior action is not invalidated, and later logic (rate/threshold check) still treats that stale state as valid, breaking the entitlement/threshold binding.

### Impact Explanation
This crosses the "a multisig request executed below threshold" boundary explicitly called out as Critical impact. A `Transfer`, `FunctionCall`, `AddKey`, `AddMember`, or `DeleteMember` action can be executed with fewer genuinely-current-member confirmations than `num_confirmations` mandates, because one "confirmation" belongs to an account that has already been removed from the multisig. In the worst case (e.g., `num_confirmations = 2` out of 3 members), a single remaining member can execute a `Transfer` of the full account balance relying on a stale confirmation from a member removed earlier, i.e., NEAR is moved by parties collectively representing fewer than the configured threshold of live signers.

### Likelihood Explanation
Likelihood is moderate to high in realistic operational flows: multisig membership changes (onboarding/offboarding signers) are a normal, expected lifecycle event, and it's plausible to have outstanding unconfirmed/partially-confirmed requests at the time a member is removed. No malicious cooperation from the removed member is required after the fact — their earlier, legitimate confirmation becomes weaponizable once they're offboarded, by any remaining member(s) who control enough further confirmations to reach the (now bypassed) threshold.

### Recommendation
When a member is removed via `delete_member` (`multisig2`) or a key is removed via `DeleteKey` (`multisig`), scan **all** pending requests' confirmation sets (not just those originated by the removed member/key) and strip out confirmation entries belonging to the removed identity. Alternatively, validate at `confirm`/execution time that every entry in a request's `confirmations` set still corresponds to a current member, discarding stale entries before comparing the count against `num_confirmations`.

### Proof of Concept
Using `multisig2` semantics, with `num_confirmations = 2` and members `{A, B, C}`:

1. `A` calls `add_request(R)` — a `Transfer` request to attacker-controlled account — creating `R` with an empty confirmation set (originating member = `A`).
2. `B` calls `confirm(R)` — `R.confirmations = {B}`, `B < 2`, so `R` stays pending.
3. Separately, `A` and `C` submit and confirm a `DeleteMember{B}` request, which executes and removes `B` from `members`. `delete_member` only clears requests where `r.member == B` (i.e., requests *created* by `B`); `R` was created by `A`, so `R.confirmations` still equals `{B}`. [5](#0-4) 
4. `A` calls `confirm(R)`. `R.confirmations.len() (1, from stale B) + 1 >= 2` → threshold satisfied → `execute_request(R)` runs the `Transfer`, even though only `A` (one live member) actually approved it after `B`'s removal. [6](#0-5) 

Funds move out of the multisig account authorized by confirmations that do not reflect two live, current members — a mis-attributed/executed-below-threshold transfer.

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
