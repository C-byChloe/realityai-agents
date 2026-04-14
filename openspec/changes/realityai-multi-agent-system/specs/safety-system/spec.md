## ADDED Requirements

### Requirement: Static risk classifier maps tools to risk levels
The system SHALL maintain a RISK_MAP dictionary that maps each tool name to a risk level (high or low). New tools SHALL default to "high" until explicitly classified. Classification SHALL complete in under 1ms.

#### Scenario: Known high-risk tool is classified
- **WHEN** the static classifier evaluates a grade_update tool call
- **THEN** it returns risk level "high" with the reason "Tool grade_update is classified as high-risk"

#### Scenario: Known low-risk tool is classified
- **WHEN** the static classifier evaluates a course_lookup tool call
- **THEN** it returns risk level "low"

#### Scenario: Unknown tool defaults to high-risk
- **WHEN** the static classifier evaluates a tool not in the RISK_MAP
- **THEN** it returns risk level "high" with the reason "Unknown tool defaults to high-risk"

### Requirement: Dynamic intent analyzer detects anomalous patterns
The system SHALL use a lightweight LLM call to analyze user intent for anomalous patterns: bulk operations (>5 records affected), scope mismatch (student requesting instructor-level actions), and adversarial intent (prompt injection, social engineering). The analyzer SHALL return structured JSON: `{ "flagged": bool, "reason": string | null }`.

#### Scenario: Bulk operation detected
- **WHEN** a user submits "Change all students' grades to A"
- **THEN** the intent analyzer returns `{ "flagged": true, "reason": "Bulk operation affecting all students" }`

#### Scenario: Normal operation passes
- **WHEN** a user submits "What time does CS101 meet?"
- **THEN** the intent analyzer returns `{ "flagged": false, "reason": null }`

#### Scenario: Privilege escalation detected
- **WHEN** a student attempts to modify another student's enrollment
- **THEN** the intent analyzer returns `{ "flagged": true, "reason": "Scope mismatch: student requesting instructor-level action" }`

### Requirement: Two-layer merge uses OR policy
The system SHALL merge results from both safety layers using an OR policy: if either the static classifier returns "high" risk OR the dynamic analyzer flags the request, the operation SHALL be flagged for HiTL review.

#### Scenario: Static flags, dynamic passes
- **WHEN** static classifier returns "high" and dynamic analyzer returns unflagged
- **THEN** the merge result is flagged with the static classifier's reason

#### Scenario: Static passes, dynamic flags
- **WHEN** static classifier returns "low" and dynamic analyzer returns flagged
- **THEN** the merge result is flagged with the dynamic analyzer's reason

#### Scenario: Both pass
- **WHEN** static classifier returns "low" and dynamic analyzer returns unflagged
- **THEN** the merge result is not flagged

### Requirement: Safety layers run in parallel
The static risk classifier and dynamic intent analyzer SHALL execute in parallel (not sequentially) to avoid adding latency on the critical path.

#### Scenario: Both layers execute concurrently
- **WHEN** a request reaches the safety check node
- **THEN** both the static classifier and dynamic analyzer are invoked concurrently, and their results are merged after both complete
