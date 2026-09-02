### Title
Stale confirmations from removed multisig members still count toward execution threshold - ([File: multisig2/src/lib.rs])

### Summary
`delete_member` in the `multisig2` contract removes a member's voting rights (deletes its access key / account membership) but does not purge that member's confirmations recorded on requests it did not itself create. `confirm()` then counts every string stored in `self.confirmations` toward `num_confirmations` without checking that each confirming identity is still a current member. A confirmation cast by a member before removal therefore continues to count after the member is removed, letting a request execute with fewer *live* confirmations than the configured threshold.

### Finding Description
`confirm()` only compares the size of the stored confirmation set to `self.num_confirmations`: [1](#0-0) 

It never re-validates that the accounts/keys already present in `confirmations` (a `HashSet<String>`) are still in `self.members`. The only place membership is enforced is `current_member()`, which is used to authorize the *caller of the current call*, not to revalidate previously stored confirmations: [2](#0-1) 

`delete_member` only cleans up confirmations for requests that the removed member itself created (`r.member == member`); it does not scan `self.confirmations` to strip that member's vote from requests created by *other* members: [3](#0-2) 

Because of this, the binding that should hold — `confirmations counted == confirmations from currently live members` — is broken: a stale confirmation from an already-removed member is indistinguishable from a live one and is summed into the threshold check at line 304.

### Impact Explanation
This is a threshold-bypass on the multisig's core security guarantee (`K` of `N` live signers must approve every privileged action — transfers, `AddKey`, `AddMember`, `FunctionCall`, etc.). An attacker (or compromised/former key holder) can get an action executed with fewer than `K` *currently authorized* approvals, i.e. "a multisig request executed below threshold," which the rules classify as Critical impact (funds moved or privileged actions executed without proper authorization).

### Likelihood Explanation
This does not require compromising a live key or foundation privileges — it only requires the ordinary, expected multisig workflow of removing a member (e.g., because their key was rotated or the person left), which is a routine `DeleteMember` operation. If any confirmed-but-unexecuted request exists at removal time, the stale confirmation silently survives and can later combine with legitimately fewer confirmations to cross the threshold. No malicious deployment, redeploy, or owner privilege abuse is required — only standard contract usage plus timing of requests around a member removal.

### Recommendation
In `confirm()`, filter `confirmations` to only members currently present in `self.members` before comparing against `num_confirmations` (or, simpler, when a member is deleted in `delete_member`, iterate all pending requests' confirmation sets and remove the deleted member's identity string from every entry, not just requests it authored). Long term, treat "confirmation count" as derived state that must always be recomputed against current membership rather than trusted as stored.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C}` and `num_confirmations = 2`.
2. Member `B` calls `add_request` to create a benign-looking pending request `R1` (e.g., a `FunctionCall`), and `A` calls `confirm(R1)` — now `confirmations[R1] = {A}` (1 of 2).
3. A separate request `R2 = DeleteMember { member: A }` is created and confirmed by `B` and `C`, executing `delete_member(A)`. Since `A` did not create `R1`, `delete_member` never touches `confirmations[R1]`; `A`'s stale confirmation remains stored.
4. `B` now calls `confirm(R1)`. `confirmations[R1].len() + 1 == 2 >= num_confirmations`, so `R1` executes — approved by only one *currently live* member (`B`) plus one stale, revoked confirmation from `A`, instead of the required two live approvals. [1](#0-0) [3](#0-2)

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

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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
