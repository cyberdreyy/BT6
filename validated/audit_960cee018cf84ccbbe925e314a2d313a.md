### Title
Removing a compromised/malicious multisig member does not purge their stale confirmations on requests created by other members, allowing a request to execute below the intended live-member threshold - (File: `multisig2/src/lib.rs`, also `multisig/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` (and the equivalent `DeleteKey` handler in the legacy `multisig` contract) is the emergency mechanism for evicting a compromised or malicious signer from the multisig. It removes the member from the `members` set and clears requests **created** by that member, but it never scans the `confirmations` map to strip that member's votes from requests **created by other members**. A removed member's stale confirmation therefore continues to count toward `num_confirmations` in `confirm`, letting a pending request execute with fewer confirmations from actual, current members than the configured threshold requires.

### Finding Description
`confirm` checks `confirmations.len() as u32 + 1 >= self.num_confirmations` and, once satisfied, executes the request via `execute_request`: [1](#0-0) 

`delete_member`, used to eject a malicious/compromised member, only removes requests whose *creator* (`r.member`) equals the member being deleted; it does not touch `self.confirmations` for requests created by anyone else: [2](#0-1) 

The same gap exists in the legacy `multisig` contract's `DeleteKey` action, which filters outstanding requests by `r.signer_pk == pk` (the request's creator key) before clearing confirmations, again ignoring confirmations that member gave on requests created by someone else: [3](#0-2) 

Binding broken: the contract's security model asserts `confirmations counted == confirmations from live members`, i.e. a request can only execute once `num_confirmations` *current* members have approved it. Once a member is removed, any confirmation they previously registered on a request created by another member remains in the `confirmations: LookupMap<RequestId, HashSet<String>>` entry forever, so `confirmations.len()` still includes votes from an account that is no longer part of the multisig's trust set. The equality holds only "before" the malicious member is removed; "after" removal, the stale vote persists and can be combined with fewer live confirmations to cross the threshold.

This is the direct analog of the external report's bug class: an emergency mechanism meant to strip a malicious actor's power (`changePrimaryStrategist` / here, `delete_member`/`DeleteKey`) is undermined because pre-existing state attributable to that actor (an outstanding strategist proposal / here, an outstanding confirmation) is not cleared when the actor is removed, letting that actor's influence still take effect afterward.

### Impact Explanation
This can allow a request — including a `Transfer` of NEAR, or an `AddKey`/`AddMember` action that hands the attacker persistent access to the multisig account — to execute despite requiring fewer live confirmations than `num_confirmations` was set to enforce. This is exactly the listed Critical impact: "a multisig request executed below threshold." Since a multisig account is presumed to hold or control funds and privileged operations, this directly threatens custody of NEAR held by the account.

### Likelihood Explanation
No special privilege is required beyond being (at some point) a legitimate multisig member/key holder — the standard trust threshold model. Any member who is later revoked (because they turned malicious, were compromised, or their key leaked) can have left behind a confirmation on some other pending request; that confirmation is never invalidated. An attacker in control of one member key only needs to confirm a request before being caught/removed, then wait or coordinate for the remaining live members to add just `num_confirmations - 1` further confirmations (rather than the full `num_confirmations`) to execute it.

### Recommendation
When a member is deleted (`delete_member` in `multisig2`, `DeleteKey` in `multisig`), iterate over all outstanding requests' `confirmations` sets (not just those the member created) and remove that member's/key's entry from each. Optionally, re-validate that the remaining confirmations still meet the threshold count of live members before allowing subsequent `confirm` calls to execute a request.

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C, D]` and `num_confirmations = 3`.
2. Member `A` calls `add_request` to create request `R` (e.g. `Transfer { amount }` to an attacker-controlled `receiver_id`, or an `AddKey` action on the multisig account itself). `R` is *not* auto-confirmed.
3. Member `D` (compromised/malicious) calls `confirm(R)` → `confirmations[R] = {D}` (len 1, below threshold).
4. Member `B` calls `confirm(R)` → `confirmations[R] = {D, B}` (len 2, still below threshold).
5. The team detects `D` is compromised and submits/confirms a `DeleteMember { member: D }` request with 3 confirmations from `A, B, C`. This executes `delete_member`, removing `D` from `members` — but since `D` did not create `R`, `R`'s entry in `confirmations` is left untouched, still containing `D`.
6. Member `C` calls `confirm(R)` → `confirmations[R].len() + 1 == 3 >= num_confirmations`, so `execute_request` runs and `R` (e.g. the `Transfer`) executes — even though only `B` and `C` are actual live members who confirmed; `D`'s stale vote (from a member already removed as malicious) supplied the third confirmation. [4](#0-3) [5](#0-4)

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
