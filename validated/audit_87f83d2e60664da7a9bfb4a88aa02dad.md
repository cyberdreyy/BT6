I found a solid analog: in `multisig2` (and the same pattern exists in `multisig` v1), confirmations recorded by a member are never purged from *other* pending requests when that member is deleted — only requests that member itself created are cleaned up. Let me confirm the exact code paths before writing up the finding.### Title
Multisig request can execute below the confirmation threshold using stale confirmations from removed members - (File: multisig2/src/lib.rs / multisig/src/lib.rs)

### Summary
`MultiSigContract::confirm()` counts entries in the `confirmations` set for a request against `self.num_confirmations` to decide when to execute a request. When a member is removed via `delete_member()` (multisig2) or a key is removed via `DeleteKey` (multisig v1), the code only purges requests that the removed member *originated*, and the associated confirmations for those requests. It never scans and strips the removed member's confirmation entries from *other* pending requests they had previously confirmed but did not create. Those stale confirmations remain counted toward the threshold, so a request can later execute with fewer *currently valid* confirming members than `num_confirmations` requires.

### Finding Description
The invariant the multisig is supposed to enforce is:

`confirmations_counted_towards_threshold == confirmations_from_current_live_members`

`confirm()` checks the live-membership only of the *caller* at confirmation time via `assert_valid_request` / `current_member()`: [1](#0-0) 

But it never re-validates that *previously stored* confirmations still belong to current members before comparing `confirmations.len() as u32 + 1 >= self.num_confirmations`: [2](#0-1) 

`delete_member()` is the only place that prunes stale confirmations, and it only does so for requests where `r.member == member`, i.e. requests *originated* by the removed member — not requests the removed member merely *confirmed*: [3](#0-2) 

So if member `B` confirms a request `R` created by member `A`, and `B` is later removed via `DeleteMember`, `R`'s `confirmations` set still contains `B`'s serialized identity. That stale confirmation continues to count toward `num_confirmations` for `R`. A later confirmation by any remaining live member can push `confirmations.len() + 1 >= num_confirmations` and trigger `execute_request`, even though the number of *currently valid* members who approved `R` is one less than the threshold requires.

The identical structural bug exists in the v1 (`multisig`) contract: `DeleteKey` only removes requests whose `signer_pk == pk` along with their confirmations, not confirmations by that key on other pending requests: [4](#0-3) 

This mirrors the reported bug class: the `liquidate()` function in the report relies on a stale flag (`warnTime > 0`) instead of re-validating the live condition (`isHealthy()`) at the time of the privileged action, letting a state change that should invalidate the action be ignored. Here, `confirm()`/`execute_request()` relies on the stale `confirmations` set instead of re-validating that each entry still corresponds to a live member at execution time.

### Impact Explanation
This breaks the multisig's core custody guarantee — that any action moving funds or changing account permissions requires `K` **currently authorized** confirmations. A request (e.g., a `Transfer`, `AddKey`/`FunctionCall`, or even another `DeleteMember`/`AddMember`) can be executed with only `K-1` live confirmations plus one stale confirmation from a removed/revoked member. This falls into the Critical impact category defined by the scope: "a multisig request executed below threshold." It effectively lets a set of members smaller than the configured threshold move NEAR or reconfigure the account, undermining the entire point of the K-of-N scheme (e.g., after off-boarding or revoking a compromised member's access, that member's earlier confirmations on any still-pending request remain valid forever).

### Likelihood Explanation
This requires no attacker outside the normal multisig member set and no special privilege beyond what any member request already has: (1) a request is created and confirmed by fewer than `K` members over time, one of whom is later removed (a routine operational action, e.g. off-boarding or revoking a suspected-compromised key), and (2) any remaining member later confirms the still-pending request. This is a realistic and likely operational sequence — request cooldown/active-request limits do not clear stale confirmations, and there is no mechanism to purge confirmations by non-originating members on `delete_member`/`DeleteKey`. No malicious deployment, redeploy, or victim key theft is required — it can occur through ordinary multisig lifecycle management (revoking a departing/compromised member) combined with normal confirmation flow.

### Recommendation
When a member/key is removed, iterate over all pending requests' `confirmations` sets (not just those the member originated) and remove the departing member's entry from each. Alternatively/additionally, in `confirm()`/`execute_request()`, re-validate that every entry in the `confirmations` set for a request still corresponds to a `self.members`-current member before comparing the count to `self.num_confirmations`, discarding stale entries at count time.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. Member `A` calls `add_request` to create request `R` (e.g., `Transfer` to an attacker-controlled account).
3. Member `B` calls `confirm(R)` → confirmations = `{B}` (1 of 3, per `confirm()` logic at [5](#0-4) ).
4. The multisig (via a separate, unrelated `DeleteMember` request reaching threshold) removes `B` as a member (e.g., because `B`'s key is suspected compromised). `delete_member()` only removes requests where `r.member == B` (requests `B` originated) — `R` was originated by `A`, so `R` and its confirmations, including `B`'s stale entry, survive: [6](#0-5) .
5. Members now are `{A, C, D}`. Member `C` calls `confirm(R)` → confirmations = `{B, C}`, so `confirmations.len() as u32 + 1 (=3) >= num_confirmations (3)` → `execute_request(R)` fires immediately.
6. `R` executes with only 1 live-member confirmation (`C`) plus 1 stale confirmation from the removed member `B`, i.e. below the intended 3-of-N live threshold.

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
