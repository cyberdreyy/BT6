### Title
Stale confirmations from removed multisig members allow request execution below the live-member confirmation threshold - (File: multisig2/src/lib.rs)

### Summary
`delete_member()` in the NEAR multisig v2 contract only purges the `requests` and `confirmations` entries for requests that the removed member *created*; it never scans other pending requests' `confirmations` sets to strip out approvals the removed member previously cast as a *co-signer*. As a result, a request can later reach `num_confirmations` and execute even though one of the counted approvals came from an account/key that is no longer a member of the multisig — i.e. the number of confirmations counted diverges from the number of confirmations from currently live members.

### Finding Description
`MultiSigContract::confirm()` decides whether to execute a request purely by counting entries in the `confirmations: LookupMap<RequestId, HashSet<String>>` set for that request: [1](#0-0) 

`delete_member()` is the only place that mutates state when a member leaves, and it removes outstanding *requests* filtered strictly by `r.member == member` (the member who created the request), then clears `num_requests_pk` and the member set itself: [2](#0-1) 

Nothing in this function (or anywhere else in the contract) iterates over `self.confirmations` to remove the departing member's `to_string()` entry from confirmation sets belonging to requests created by *other* members. Once `member` is removed from `self.members`, `current_member()` will never again resolve to that member, so they can no longer confirm new requests — but their old confirmation, sitting inside a still-pending request's `HashSet<String>`, is never invalidated and keeps counting toward `num_confirmations`.

The binding that should hold is:
```
confirmations.len() for request R == number of *currently live* members who approved R
```
After a member removal, this becomes:
```
confirmations.len() for request R == (live members who approved R) + (removed members who approved R before deletion)
```
i.e. claims of approval exceed the approvals actually available from the current member set.

### Impact Explanation
This is Critical: it allows a `MultiSigRequest` (including a `Transfer` of NEAR, an `AddKey`/`AddMember` granting new access, or a `FunctionCall`) to be executed with fewer than `num_confirmations` genuine approvals from current members — i.e. "a multisig request executed below threshold." A malicious or compromised member need not obtain independent approval from enough currently-trusted members; they can reuse a stale approval left behind by a member who was later removed (e.g., for being compromised or for being replaced), to push a self-serving transfer or key addition through the multisig with insufficient live consensus.

### Likelihood Explanation
This is reachable through completely ordinary multisig usage: (1) member membership changes over time (member offboarding/key rotation is an expected, first-class operation via `DeleteMember`), and (2) it's common for a request to receive partial confirmations and stay pending for a while (the contract explicitly supports partial-confirmation state and even an `active_requests_limit`/cooldown mechanism assuming pending requests persist). Any request that collects at least one confirmation before its creator (or another confirmer) is later removed from the multisig is exposed. No privileged access beyond being a current multisig member is required to trigger execution once the stale confirmation exists.

### Recommendation
When removing a member in `delete_member()`, iterate over all entries in `self.confirmations` (not just requests the member created) and remove the member's `to_string()` key from every confirmation `HashSet`. Equivalently, before counting confirmations in `confirm()`/at request-creation validity checks, filter the stored confirmation set to only members still present in `self.members` before comparing against `num_confirmations`. The `AggregateStablePrice`-style fix (clean up the auxiliary tracking structure on removal, not just the primary one) applies here to `confirmations` alongside `requests` and `num_requests_pk`.

### Proof of Concept
1. Deploy multisig2 with members `[A, B, C, D]` and `num_confirmations = 3`.
2. Member `C` calls `add_request` for `Transfer { amount }` to `receiver_id` → `request_id = R`.
3. Member `A` calls `confirm(R)` → `confirmations[R] = {A}`.
4. Member `B` calls `confirm(R)` → `confirmations[R] = {A, B}` (2 < 3, stays pending).
5. Separately, members `B, C, D` submit and confirm a `DeleteMember { member: A }` request (3-of-4 threshold met), which executes `delete_member` — this removes `A` from `self.members` and deletes any requests *A created*, but request `R` (created by `C`) is untouched, so `confirmations[R]` still equals `{A, B}`.
6. Now only `B, C, D` are members (3 members, `num_confirmations` still 3). Member `C` calls `confirm(R)`: `confirmations.len() (2) + 1 >= 3` is true, so `execute_request` runs the `Transfer`, even though the actual live-member approvals are only `B` and `C` — one short of the required 3-of-3 live-member threshold. [3](#0-2) [4](#0-3)

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
