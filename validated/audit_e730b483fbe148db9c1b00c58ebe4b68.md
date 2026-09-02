Confirmed: `delete_member` at lines 356-379 in `multisig2/src/lib.rs` only removes confirmations for requests originally *added* by the deleted member (filtering `r.member == member`), but never scrubs the deleted member's confirmation entries from `confirmations` sets on requests that were added by *other* still-existing members. A stale confirmation from a removed member remains counted toward `num_confirmations` in `confirm()` (line 304: `confirmations.len() as u32 + 1 >= self.num_confirmations`), letting a request execute with fewer live-member approvals than the threshold requires.

### Title
Stale confirmations from removed multisig members remain counted toward the confirmation threshold, allowing execution below quorum - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` authorizes execution of a `MultiSigRequest` once the number of entries in `confirmations` reaches `num_confirmations` [1](#0-0) . `delete_member` is supposed to purge a departing member's influence, but it only removes confirmations for requests that the *deleted* member itself originally created (`r.member == member`); it never scans `confirmations` sets belonging to requests created by *other* members where the deleted member had already cast a confirmation [2](#0-1) .

### Finding Description
The intended custody binding is: `confirmations.len() for request R == number of currently-live members who approved R`. This invariant is what gives the K-of-N guarantee its meaning — that a request can only execute once K *live* members approved it.

Concretely:
1. Member A creates request R (`add_request`), requesting a `Transfer` or `FunctionCall`.
2. Member B (a distinct, currently valid member) calls `confirm(R)`, which is below threshold, so B's entry is added to `self.confirmations` for R: `confirmations.insert(member.to_string()); self.confirmations.insert(&request_id, &confirmations);` [3](#0-2) .
3. Separately, a self-request `DeleteMember { member: B }` is confirmed and executed, calling `delete_member(promise, B)` [4](#0-3) . This function only removes confirmations for requests where `r.member == B` (i.e. requests B *created*), and removes B from `self.members` and `num_requests_pk`, but does **not** search `self.confirmations` values for other requests (like R, created by A) that contain B's stale entry [5](#0-4) .
4. B is now fully removed from `self.members` and has no ability to sign new transactions (their key/account is gone), yet the stale confirmation entry for R still exists in `self.confirmations.get(&R)`.
5. Later, enough remaining live members confirm R. `confirm()` checks `confirmations.len() as u32 + 1 >= self.num_confirmations`, counting B's stale confirmation as if it were live [6](#0-5) . This allows R (e.g., a `Transfer` of contract funds, or `AddKey`/`FunctionCall`) to execute with one fewer *live* signer than `num_confirmations` requires.

This breaks the "confirmations counted versus live members" custody binding: the equality `count(confirmations) == count(live approving members)` no longer holds once a confirming member is deleted after confirming, but before the request is executed or otherwise touched.

### Impact Explanation
This allows execution of a multisig request (including fund `Transfer`, `AddKey`, or `FunctionCall` actions) below the actual number of currently-authorized, live members — i.e., a multisig request executed below threshold. This matches the Critical impact category defined for this analysis (multisig request executed below threshold), because it permits the contract's custodied NEAR/actions to be moved or altered without the full K-of-N live authorization the contract is supposed to guarantee.

### Likelihood Explanation
The precondition is a normal operational sequence for any multisig that periodically rotates members: (a) a member confirms one or more pending, not-yet-executed requests, then (b) that member is later removed via `DeleteMember` (e.g., for key rotation, off-boarding, or compromise response) while other requests they confirmed are still outstanding. Given `active_requests_limit` allows up to `ACTIVE_REQUESTS_LIMIT` (12) simultaneous outstanding requests per member [7](#0-6) , and no cleanup pass over `confirmations` occurs on member removal beyond the deleted member's own requests, this is straightforward for a remaining member (or a member being removed themselves, if they can front-run the deletion) to exploit without any flash loan, price manipulation, or external dependency — the request just needs to remain unresolved when the member is deleted.

### Recommendation
When deleting a member, iterate over *all* outstanding requests' confirmation sets (not only those authored by the deleted member) and remove the departing member's identity string from each. Alternatively, revalidate at `confirm()`/execution time that every entry in a request's `confirmations` set still corresponds to a current member in `self.members`, discarding stale entries (and adjusting the threshold check) before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(members: [A, B, C, D], num_confirmations: 3)`.
2. As member A: `add_request_and_confirm(Transfer{amount, receiver_id: attacker})` → request `R` has 1 confirmation (A).
3. As member B: `confirm(R)` → `R` now has 2 confirmations (A, B), still below 3, so it stays pending.
4. As members (self-request, 3 confirmations reached): create and confirm `DeleteMember{member: B}` targeting `receiver_id = current_account_id()`. This executes `delete_member(promise, B)`, which removes B from `self.members` and `num_requests_pk`, and deletes only requests where `r.member == B` — request `R` (authored by A) is untouched, and `self.confirmations.get(&R)` still contains B's entry.
5. As member C: `confirm(R)`. `confirmations.len()` was 2 (A, B) → now becomes 3 (A, B, C) which is `>= num_confirmations(3)`, so `execute_request` runs the `Transfer` to `attacker`.
6. The request executed with only 2 live members (A and C) actually authorizing it at execution time — B was already removed — despite the contract requiring 3-of-4 confirmations. [1](#0-0) [2](#0-1)

### Citations

**File:** multisig2/src/lib.rs (L20-20)
```rust
const ACTIVE_REQUESTS_LIMIT: u32 = 12;
```

**File:** multisig2/src/lib.rs (L239-242)
```rust
                MultiSigRequestAction::DeleteMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.delete_member(promise, member)
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
