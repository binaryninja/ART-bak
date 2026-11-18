---
name: servicenow-public-rest
description: Assess scripted REST operations that do not require authentication on an admitted ServiceNow instance, using inventory evidence and the versioned public REST assessment.
---

Use `list_assessments` to find the instance's inventory and `read_assessment` to inspect its status. Submit `servicenow.inventory` if inventory is missing. Submit `servicenow.public_rest` for the admitted instance, then report the returned task URL. Read its persisted report before claiming that the assessment completed.

An operation's `requires_authentication=false` flag is only one part of its authorization. Examine API-level settings, URI-pattern REST ACLs and their roles, API access policies, record and field ACLs, and application-level JWT or signature gates. Protected source and denied metadata are explicit limitations.

Classify every operation by attacker-controlled inputs, accessed records or files, response and log disclosure, external calls, and state-changing behavior. Source-reviewed handler probes must match the current route, method, and source hash. Unknown or changed handlers receive metadata-only checks until reviewed. Existing-object authorization tests require a named, non-sensitive fixture. Do not turn an invalid-input result into a claim that a successful request is safe or exploitable.

Keep task and generation IDs, source hashes, request method/path/authentication mode, response hashes, expected behavior, observed behavior, and limitations together. Credentials, cookies, and secret header values belong in private host-managed storage. Report confirmed findings separately from candidate attack surfaces and untested behavior.
