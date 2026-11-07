# DuoHound
A BloodHound OpenGraph extension for Cisco Duo Security MFA

<img width="1251" height="679" alt="duo_user_details" src="https://github.com/user-attachments/assets/39d044d9-4b7e-45a6-9662-99dd2f4a991a" />


## Overview

DuoHound extracts data from Duo Security's Admin API and converts it into BloodHound's OpenGraph format. This data can be used to visualize MFA relationships, identify enrollment gaps, and analyze application access paths.

## Installation

DuoHound uses the official Duo Security Python client, found here: https://github.com/duosecurity/duo_client_python

```bash
pip install duo-client
```

## Setup

### Create Duo Admin API Application

1. Log in to the Duo Admin Panel
2. Navigate to **Applications** → **Protect an Application**
3. Search for "Admin API" and click **Protect**
4. Set permissions to **Grant Resource - Read, Grant Administrators - Read, Grant Read Information**
5. Note your **Integration key**, **Secret key**, and **API hostname**

## Usage

### Basic

```bash
python duohound.py \
  --integration-key YOUR_IKEY \
  --secret-key YOUR_SKEY \
  --api-hostname api-XXXXXXXX.duosecurity.com
```

### Options

| Flag | Description |
|------|-------------|
| `-o, --output` | Output filename (default: `duo_bloodhound_data.json`) |
| `-v, --verbose` | Enable verbose logging |
| `--version` | Show version |


### Import into BloodHound

1. Upload [DuoHound-Community-v1.0.0.zip](extension/DuoHound-Community-v1.0.0.zip) through [OpenGraph Management](https://bloodhound.specterops.io/opengraph/extensions/manage).
2. Run the collector and upload `duo_bloodhound_data.json` through BloodHound's data upload modal.
3. Explore the collected graph.

The bundle contains [schema.json](extension/schema.json). Install it separately
from graph data. No Privilege Zone rules are included.

### Upgrading existing data

Owned kinds now use the `Duo_` namespace, for example `DuoUser` becomes `Duo_User`
and `DuoMemberOf` becomes `Duo_MemberOf`. The data source changes from `DuoBase`
to `Duo`; object IDs are unchanged. Recollect and import updated data after
installing the bundle, and update custom Cypher references. Importing new data
does not automatically remove legacy labels or relationships; review any legacy
data cleanup separately.

For future component changes, increment `schema.version`, commit a matching
Community ZIP, update its README link, and upload the new bundle through
OpenGraph Management to update the installed extension.

## Data Model

```mermaid
graph LR
    subgraph Nodes
        Account[Duo_Account]
        User[Duo_User]
        Admin[Duo_Admin]
        Group[Duo_Group]
        App[Duo_Application]
        Phone[Duo_Phone]
        Token[Duo_Token]
        WebAuthn[Duo_WebAuthnCredential]
    end

    Account -->|Duo_Contains| User
    Account -->|Duo_Contains| Admin
    Account -->|Duo_Contains| Group
    Account -->|Duo_Contains| App
    Account -->|Duo_Contains| Phone
    Account -->|Duo_Contains| Token
    Account -->|Duo_Contains| WebAuthn

    Admin -->|Duo_AdminTo| Account
    User -->|Duo_MemberOf| Group
    User -->|Duo_HasAccessTo| App
    Group -->|Duo_HasAccessTo| App
    User -->|Duo_HasMFADevice| Phone
    User -->|Duo_HasMFADevice| Token
    User -->|Duo_HasMFADevice| WebAuthn
```

### Node Properties

| Node | Key Properties |
|------|----------------|
| Duo_Account | `api_hostname`, `account_type` |
| Duo_User | `email`, `status`, `is_enrolled`, `token_count`, `webauthn_count` |
| Duo_Admin | `email`, `role`, `status` |
| Duo_Group | `description`, `push_enabled`, `sms_enabled` |
| Duo_Application | `type`, `integration_key` |
| Duo_Phone | `number`, `platform`, `activated`, `capabilities` |
| Duo_Token | `token_type`, `serial` |
| Duo_WebAuthnCredential | `label`, `authenticator_type` |

### Relationship semantics

| Relationship | Direction | Traversable | Meaning |
|---|---|---|---|
| `Duo_Contains` | Account → object | No | Organizational containment. |
| `Duo_AdminTo` | Administrator → account | No | Association for all roles and statuses; does not assert tenant control. |
| `Duo_MemberOf` | User → group | Yes | User inherits the group's application eligibility, subject to authentication requirements. |
| `Duo_HasAccessTo` | User/group → application | No | Configured authentication eligibility, not application control. |
| `Duo_HasMFADevice` | User → MFA device | No | Device registration does not establish device possession or control. |

Application access configuration does not prove successful authentication or
control of the protected service. Primary credentials, user state, MFA and policy
remain prerequisites. `Duo_AdminTo` includes limited and disabled administrators;
role-specific control paths are not modeled. MFA registration is deliberately
represented from user to device and is not an impersonation edge.

## Example Queries

### All Duo Admins

```cypher
MATCH p= (:Duo_Admin)-[:Duo_AdminTo]->(:Duo_Account)
RETURN p
```

### Users that are members of Groups

```cypher
MATCH p=(u:Duo_User)-[:Duo_MemberOf]->(:Duo_Group)
RETURN p
```

### Users with bypass status (no MFA required)

```cypher
MATCH (u:Duo_User)
WHERE u.status = 'bypass'
RETURN u
```

### Unenrolled users

```cypher
MATCH (u:Duo_User)
WHERE u.is_enrolled = false
RETURN u
```

### Applications accessible by all users (not limited by groups)

```cypher
MATCH p=(u:Duo_User)-[r:Duo_HasAccessTo]->(a:Duo_Application)
WHERE r.access_type = 'all_users'
RETURN p
```

### Access paths to a specific application

```cypher
MATCH p = (u:Duo_User)-[*1..]->(a:Duo_Application)
WHERE a.name contains 'VPN'
RETURN p
```
<img width="1768" height="193" alt="user_application_access" src="https://github.com/user-attachments/assets/08ce69e8-0d14-4849-b2b6-a66f52d2a3a1" />

## Security Insights

| Finding | Query Filter | Risk |
|---------|--------------|------|
| Bypass users | `status = 'bypass'` | Can authenticate without MFA |
| Lack of group-restricted access | `access_type = 'all_users'` | Overly permissive app access |
| Unenrolled users | `is_enrolled = false` | No MFA devices registered |
| Weak MFA factor | `capabilities = 'sms'` | Relies on sms-based MFA only |

## Troubleshooting

**No data returned**
- Verify API credentials
- Ensure API application has read permissions
- Check hostname format: `api-XXXXXXXX.duosecurity.com`

## Future Enhancement Ideas

DuoHound is only collecting a fraction of data available in Duo Security today. Future enhancements will include extending collection to Endpoints, Admin Units, Duo Directory, and Policies.

## Disclaimer

For authorized security testing only. Ensure proper authorization before running against any system.
