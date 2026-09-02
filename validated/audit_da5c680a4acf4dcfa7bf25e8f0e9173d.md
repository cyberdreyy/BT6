### Title
Stale Confirmations From Removed Members Allow Multisig Requests To Execute Below The Live-Member Threshold - (File: `multisig2/src/lib.rs`)

### Summary
`multisig2`'s `confirm()` counts entries in the `confirmations: LookupMap<RequestId, HashSet<String>>` map to decide when a request has reached `num_confirmations`. `delete_member()` only purges *requests originated by* the removed member; it never scrubs that member's *confirmation entries* recorded on other, still-pending requests. As a result, a member who is later removed from the multisig can leave behind a "ghost" confirmation that still counts toward the threshold, letting a request (e.g. an arbitrary NEAR transfer) execute with fewer genuinely-authorized, currently-live members than `num_confirmations` requires.

### Finding Description
The invariant the multisig is supposed to enforce is:
`number of confirmations from members who ARE members at execution time == num_confirmations` (or greater).

`confirm()` breaks this by trusting the raw size of the stored `HashSet<String>` without re-validating that each recorded confirmer is still in `self.members`: [1](#0-0) 

`delete_member()` is the only place membership removal happens, and it removes:
- outstanding requests filed **by** the removed member (`r.member == member`)
- the removed member's `num_requests_pk` counter
- the removed member itself from `self.members`

but it does **not** iterate `self.confirmations` to strip the removed member's entry from requests that member merely *confirmed* (but did not originate): [2](#0-1) 

Because `MultiSigRequestWithSigner.member` only records the *originator* of a request, `delete_member`'s filter (`r.member == member`) cannot find or clean up confirmations added by that member on someone else's request. The confirmation string previously inserted in `confirm()`'s `else` branch remains in the `HashSet<String>` forever (or until the request is otherwise deleted/executed): [1](#0-0) 

`assert_valid_request()` also does not help — it only validates that the *caller* of the current `confirm`/`delete` call is a member; it never re-checks the members already present in the stored confirmation set: [3](#0-2) 

### Impact Explanation
This breaks the custody/authorization binding "confirmations counted versus live members." A request that should require `num_confirmations` currently-authorized signers can execute with one fewer *live* confirmation, because a stale confirmation from a since-removed member is still counted. Since `execute_request` can perform `Transfer`, `FunctionCall`, `AddKey`/`AddFullAccessKey`, etc. against the multisig's own NEAR balance, this allows a minority coalition of live members (fewer than the configured threshold) to move NEAR held by the multisig account, i.e. **a multisig request executed below threshold** — explicitly listed as Critical impact.

### Likelihood Explanation
This requires no external validator/foundation/redeploy — it is reachable purely through the multisig's own public methods (`add_request`, `confirm`, and `DeleteMember` action, all standard multisig operations any member can initiate/participate in). Any sequence where (a) a request accumulates a confirmation from member X, (b) member X is later removed via a separate `DeleteMember` request, and (c) the original request is subsequently confirmed by others, triggers the bug. This is a realistic operational sequence (member rotation is a normal multisig lifecycle event) rather than a contrived edge case.

### Recommendation
When executing `delete_member`, iterate over `self.confirmations` for *all* pending requests (not just those the member authored) and remove the deleted member's entry from each confirmation set. Alternatively, in `confirm()`/`assert_valid_request()`, filter/recompute the confirmation count by intersecting the stored confirmer set with `self.members` before comparing against `num_confirmations`, so stale entries from removed members never count toward the threshold.

### Proof of Concept
Assume members `{A, B, C, D, E}`, `num_confirmations = 3`.

1. `A` calls `add_request_and_confirm(R1)` where `R1 = Transfer { amount: X }`. `confirmations[R1] = {A}`.
2. `B` calls `confirm(R1)`. `1 + 1 = 2 < 3`, so it is only recorded: `confirmations[R1] = {A, B}`.
3. Separately, members `C`, `D`, `E` create and confirm request `R2 = DeleteMember { member: B }` (a self-request reaching the 3-confirmation threshold with genuinely live members). `delete_member(B)` runs:
   - It scans `self.requests` for requests where `member == B` — `R1`'s `member` field is `A` (the originator), so `R1`'s confirmations are **not** touched.
   - `B` is removed from `self.members`. Current members are now `{A, C, D, E}`.
4. `C` (a live, current member) calls `confirm(R1)`. `assert_valid_request` passes (C is a current member). `confirmations[R1] = {A, B}`, `len() == 2`, `2 + 1 = 3 >= num_confirmations (3)` → `execute_request(R1)` runs, transferring `X` NEAR out of the multisig.

Only `A` and `C` are actual current members who authorized this specific execution (`B`'s confirmation is stale, from a removed member), yet the transfer executes as if 3 live confirmations were obtained, bypassing the configured threshold with `numConfirmations - 1` genuine confirmations.

### Citations

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
