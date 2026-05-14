# Drift Detection Report

**Document**: /home/ubuntu/lab-screenshot/enablement-drift/guides/seeded-drift-guide.md
**Org**: https://taskvantage-admin.okta.com
**Run**: test-204400
**Time**: 2026-05-14T20:46:14.625114

## Summary

- **Steps checked**: 5
- **Labels checked**: 13
- **Drift found**: 11
- **Auto-mergeable**: 1
- **Needs review**: 10

## Findings

### 🔴 Finding 1: missing_element (step step-1)

- **Severity**: high
- **Confidence**: 85%
- **Expected**: Identity Governance
- **Observed**: (not found)
- **Auto-merge eligible**: No
- **Reasoning**: Expected label "Identity Governance" not found in any captured UI element or page text

### 🟢 Finding 2: ambiguous (step step-1)

- **Severity**: low
- **Confidence**: 60%
- **Expected**: Access Certifications
- **Observed**: (found in page text but not as a discrete UI element)
- **Auto-merge eligible**: No
- **Reasoning**: Text "Access Certifications" found in page body but not matched to a specific UI element

### 🟢 Finding 3: ambiguous (step step-1)

- **Severity**: low
- **Confidence**: 60%
- **Expected**: Access Certifications
- **Observed**: (found in page text but not as a discrete UI element)
- **Auto-merge eligible**: No
- **Reasoning**: Text "Access Certifications" found in page body but not matched to a specific UI element

### 🟡 Finding 4: label_rename (step step-2)

- **Severity**: medium
- **Confidence**: 94%
- **Expected**: Campaigns
- **Observed**: Campaign
- **Suggested fix**: Replace "Campaigns" with "Campaign"
- **Auto-merge eligible**: Yes
- **Reasoning**: Fuzzy match (94% similarity): "Campaigns" ≈ "Campaign"

### 🔴 Finding 5: missing_element (step step-3)

- **Severity**: high
- **Confidence**: 85%
- **Expected**: Review progress
- **Observed**: (not found)
- **Auto-merge eligible**: No
- **Reasoning**: Expected label "Review progress" not found in any captured UI element or page text

### 🔴 Finding 6: missing_element (step step-3)

- **Severity**: high
- **Confidence**: 85%
- **Expected**: Pending reviews
- **Observed**: (not found)
- **Auto-merge eligible**: No
- **Reasoning**: Expected label "Pending reviews" not found in any captured UI element or page text

### 🔴 Finding 7: missing_element (step step-3)

- **Severity**: high
- **Confidence**: 85%
- **Expected**: Approved
- **Observed**: (not found)
- **Auto-merge eligible**: No
- **Reasoning**: Expected label "Approved" not found in any captured UI element or page text

### 🔴 Finding 8: missing_element (step step-3)

- **Severity**: high
- **Confidence**: 85%
- **Expected**: Revoked
- **Observed**: (not found)
- **Auto-merge eligible**: No
- **Reasoning**: Expected label "Revoked" not found in any captured UI element or page text

### 🔴 Finding 9: missing_element (step step-3)

- **Severity**: high
- **Confidence**: 85%
- **Expected**: End campaign
- **Observed**: (not found)
- **Auto-merge eligible**: No
- **Reasoning**: Expected label "End campaign" not found in any captured UI element or page text

### 🔴 Finding 10: missing_element (step step-4)

- **Severity**: high
- **Confidence**: 85%
- **Expected**: Preconfigured campaigns
- **Observed**: (not found)
- **Auto-merge eligible**: No
- **Reasoning**: Expected label "Preconfigured campaigns" not found in any captured UI element or page text

### 🔴 Finding 11: missing_element (step step-5)

- **Severity**: high
- **Confidence**: 85%
- **Expected**: Security access reviews
- **Observed**: (not found)
- **Auto-merge eligible**: No
- **Reasoning**: Expected label "Security access reviews" not found in any captured UI element or page text
