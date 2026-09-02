### Title
Stale confirmations from a removed multisig member remain counted toward the approval threshold, allowing a request to execute below the live-member threshold - ([File: multisig2/src/lib.rs], [File: multisig/src/lib.rs])

### Summary
`delete_member` (in `multisig2`) and `DeleteKey` (in `multisig`) purge outstanding *requests originated by* the removed member/key, but never scrub that member's/key's existing *confirmations* on requests they merely co-signed as a confirmer. `confirm()` counts the size of the stored confirmation set against `num_confirmations` without validating that every entry still belongs to a current member, so a stale confirmation from a member that has since been removed continues to count toward the threshold, letting a request execute with fewer genuinely authorized (live) confirmations than `num_confirmations` requires.

### Finding Description
In `multisig2/src/lib.rs`, `delete_member` only removes requests where the removed member is the **submitter** (`r.member == member`): [1](#0-0) 

It does not scan `self.confirmations` for entries that contain the removed member's identity as a **confirmer** of other, still-pending requests. Those confirmation `HashSet<String>` entries are left untouched: [2](#0-1) 

`confirm()` trusts the raw cardinality of the confirmation set (`confirmations.len() as u32 + 1 >= self.num_confirmations`) to decide whether to execute the request, with no check that every public key/account in that set is still present in `self.members`.

The same class of bug exists in the older `multisig/src/lib.rs`: `DeleteKey` only clears requests where the removed key is the original signer (`r.signer_pk == pk`), leaving that key's confirmations on other pending requests intact: [3](#0-2) [4](#0-3) 

This breaks the intended custody binding: `confirmations_counted(request) == confirmations_by_live_members(request)`. Once a member is removed, any confirmation they previously placed on a request they did not submit becomes a "ghost" vote that is still tallied.

### Impact Explanation
This is Critical per the rubric: "a multisig request executed below threshold." A `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, or `DeployContract` action can be executed by the multisig with one fewer genuinely-authorizing live member than `num_confirmations` specifies, because a stale confirmation from a removed member is counted as if it were live. This directly weakens the custody guarantee the multisig account is supposed to enforce over NEAR held in the account (and over which keys/members can control it), since funds can move or membership/config can change without the documented number of currently-trusted parties actually agreeing.

### Likelihood Explanation
The only precondition is the ordinary, expected sequence of multisig operations: (1) a request is created and partially confirmed by multiple members, (2) before it reaches the confirmation threshold, one of its confirmers is removed via `DeleteMember`/`DeleteKey` (a routine key-rotation/offboarding action explicitly supported by the contract), and (3) the request is later confirmed by enough of the *remaining* members to reach the raw count, even though it doesn't reach the count of currently live confirmers. No attacker key or malicious party is even strictly required — this can happen through normal multisig operational hygiene (rotating out a departing member) — but it also gives a departing/compromised member's earlier confirmation lingering, unintended weight, and a colluding subset of remaining members can deliberately exploit it to push through a request that should require one more live confirmation.

### Recommendation
When removing a member (`delete_member`/`DeleteKey`), iterate over all entries in `self.confirmations` (not just requests where the removed member is the submitter) and remove the removed member's identity from every confirmation set. Alternatively, when checking the threshold in `confirm()`, filter the confirmation set to only those entries still present in `self.members` before comparing its length to `self.num_confirmations`.

### Proof of Concept
1. Deploy multisig2 with `num_confirmations = 3` and members `[A, B, C, D]`.
2. `A` calls `add_request_and_confirm(R)` where `R` is `Transfer{amount}` — `confirmations(R) = {A}`.
3. `B` calls `confirm(R)` — `confirmations(R) = {A, B}` (len 2, below threshold, request stays pending).
4. Separately, members legitimately reach threshold on another request to `DeleteMember(B)` (e.g., `A`, `C`, `D` confirm it) — `B` is removed from `self.members`. `delete_member` only clears requests where `B` was the *submitter*; `R` (submitted by `A`) is untouched, so `confirmations(R)` still contains `B`.
5. `C` calls `confirm(R)` — `confirmations(R).len() + 1 = 3 >= num_confirmations (3)` — `R` executes via `execute_request`, transferring funds, even though only `A` and `C` are actually live, confirming members (2 live confirmations, not 3). [5](#0-4) [6](#0-5)

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
