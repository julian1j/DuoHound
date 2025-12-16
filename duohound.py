#!/usr/bin/env python3
"""
DuoHound v4 - Duo Admin API to BloodHound Import Script

Extracts users, groups, applications, administrators, and phones from Duo's Admin API
and structures them as nodes and edges for BloodHound import.

Requirements:
- pip install duo-client
- Valid Duo Admin API credentials
"""

__version__ = "4.0.0"

import json
import logging
import argparse
import sys
from typing import Dict, List, Any, Optional, Callable

try:
    from duo_client.admin import Admin
except ImportError:
    print("Error: duo-client not installed. Run: pip install duo-client")
    sys.exit(1)


class DuoToBloodHoundConverter:
    GROUP_FIELDS = ['groups_allowed', 'PERMITTED_GROUPS', 'permitted_groups', 'allowed_groups']
    SKIP_VALUES = {'NO_USERS', 'NONE', 'NULL', 'EMPTY', '', 'NO_GROUPS'}

    def __init__(self, integration_key: str, secret_key: str, api_hostname: str, skip_mfa_details: bool = False):
        self.admin_api = Admin(ikey=integration_key, skey=secret_key, host=api_hostname)
        self.nodes = []
        self.edges = []
        self.skip_mfa_details = skip_mfa_details
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)

    # --- Helpers ---

    def _unwrap(self, response: Any) -> List[Dict[str, Any]]:
        return response if isinstance(response, list) else response.get('response', response)

    def _clean(self, props: Dict[str, Any], keep_empty: bool = False) -> Dict[str, Any]:
        if keep_empty:
            return {k: v for k, v in props.items() if v is not None}
        return {k: v for k, v in props.items() if v is not None and v != ''}

    def _paginated_fetch(self, fetch_fn: Callable, name: str, limit: int = 300) -> List[Dict[str, Any]]:
        all_items, offset = [], 0
        self.logger.info(f"Fetching {name} from Duo Admin API...")
        while True:
            try:
                items = self._unwrap(fetch_fn(limit=limit, offset=offset))
                if not items:
                    break
                all_items.extend(items)
                self.logger.info(f"Retrieved {len(items)} {name} (total: {len(all_items)})")
                if len(items) < limit:
                    break
                offset += limit
            except Exception as e:
                self.logger.error(f"Error fetching {name}: {e}")
                break
        return all_items

    def _simple_fetch(self, fetch_fn: Callable, name: str) -> List[Dict[str, Any]]:
        try:
            self.logger.info(f"Fetching {name} from Duo Admin API...")
            items = self._unwrap(fetch_fn())
            self.logger.info(f"Retrieved {len(items)} {name}")
            return items
        except Exception as e:
            self.logger.error(f"Error fetching {name}: {e}")
            return []

    def _should_skip(self, val: str) -> bool:
        return val.upper() in self.SKIP_VALUES

    # --- Node/Edge Creation ---

    def create_node(self, node_id: str, kind: str, name: str, props: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": node_id, "kinds": [kind], "properties": {"name": name, **props}}

    def create_edge(self, start: str, end: str, kind: str, props: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        edge = {"kind": kind, "start": {"value": start, "match_by": "id"}, "end": {"value": end, "match_by": "id"}}
        if props:
            edge["properties"] = props
        return edge

    # --- Data Fetching ---

    def get_all_users(self) -> List[Dict[str, Any]]:
        return self._paginated_fetch(self.admin_api.get_users, "users")

    def get_all_phones(self) -> List[Dict[str, Any]]:
        return self._paginated_fetch(self.admin_api.get_phones, "phones")

    def get_all_groups(self) -> List[Dict[str, Any]]:
        return self._simple_fetch(self.admin_api.get_groups, "groups")

    def get_all_admins(self) -> List[Dict[str, Any]]:
        return self._simple_fetch(self.admin_api.get_admins, "administrators")

    def get_all_applications(self) -> List[Dict[str, Any]]:
        for method in ['get_applications', 'get_integrations']:
            if hasattr(self.admin_api, method):
                return self._simple_fetch(getattr(self.admin_api, method), "applications")
        self.logger.warning("No applications/integrations method found")
        return []

    def get_group_users(self, group_id: str) -> List[str]:
        try:
            return [u['user_id'] for u in self._unwrap(self.admin_api.get_group_users(group_id)) if u.get('user_id')]
        except Exception as e:
            self.logger.error(f"Error fetching users for group {group_id}: {e}")
            return []

    def get_user_tokens(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            return self._unwrap(self.admin_api.get_user_tokens(user_id)) or []
        except Exception as e:
            self.logger.debug(f"Error fetching tokens for user {user_id}: {e}")
            return []

    def get_user_webauthn_credentials(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            return self._unwrap(self.admin_api.get_user_webauthn_credentials(user_id)) or []
        except Exception as e:
            self.logger.debug(f"Error fetching WebAuthn for user {user_id}: {e}")
            return []

    # --- Processing ---

    def process_users(self, users: List[Dict[str, Any]]) -> None:
        self.logger.info(f"Processing {len(users)} users...")
        if self.skip_mfa_details:
            self.logger.info("Skipping detailed MFA device queries")

        for idx, user in enumerate(users, 1):
            user_id, username = user.get('user_id', ''), user.get('username', '')
            if not user_id or not username:
                continue
            if idx % 10 == 0:
                self.logger.info(f"Processing user {idx}/{len(users)}: {username}")

            tokens, webauthn = [], []
            if not self.skip_mfa_details:
                tokens = self.get_user_tokens(user_id)
                webauthn = self.get_user_webauthn_credentials(user_id)

            props = self._clean({
                'email': user.get('email', ''), 'real_name': user.get('realname', ''),
                'status': user.get('status', ''), 'created': user.get('created', ''),
                'last_login': user.get('last_login'), 'is_enrolled': user.get('is_enrolled', False),
                'last_directory_sync': user.get('last_directory_sync'),
                'notes': user.get('notes', ''), 'duo_user_id': user_id
            }, keep_empty=True)

            if not self.skip_mfa_details:
                props.update({'token_count': len(tokens), 'has_tokens': len(tokens) > 0,
                              'webauthn_count': len(webauthn), 'has_webauthn': len(webauthn) > 0})

            self.nodes.append(self.create_node(user_id, "DuoUser", username, props))

            if not self.skip_mfa_details:
                self._process_tokens(user_id, tokens)
                self._process_webauthn(user_id, webauthn)

        self.logger.info(f"Completed processing {len(users)} users")

    def _process_tokens(self, user_id: str, tokens: List[Dict[str, Any]]) -> None:
        for t in tokens:
            tid = t.get('token_id', '')
            if not tid:
                continue
            ttype, serial = t.get('type', ''), t.get('serial', '')
            name = f"{ttype} - {serial}" if serial and ttype else (serial or ttype or tid)
            props = self._clean({'token_type': ttype, 'serial': serial, 'totp_step': t.get('totp_step'), 'duo_token_id': tid})
            self.nodes.append(self.create_node(tid, "DuoToken", name, props))
            self.edges.append(self.create_edge(user_id, tid, "DuoHasMFADevice"))

    def _process_webauthn(self, user_id: str, creds: List[Dict[str, Any]]) -> None:
        for c in creds:
            cid = c.get('webauthnkey', '')
            if not cid:
                continue
            label = c.get('label', '')
            props = self._clean({'label': label, 'date_added': c.get('date_added'),
                                 'authenticator_type': c.get('authenticator_type', ''),
                                 'credential_name': c.get('credential_name', ''), 'duo_webauthn_id': cid})
            self.nodes.append(self.create_node(cid, "DuoWebAuthnCredential", label or cid, props))
            self.edges.append(self.create_edge(user_id, cid, "DuoHasMFADevice"))

    def process_groups(self, groups: List[Dict[str, Any]]) -> None:
        self.logger.info("Processing groups...")
        for g in groups:
            gid, name = g.get('group_id', ''), g.get('name', '')
            if not gid or not name:
                continue
            props = self._clean({
                'description': g.get('desc', ''), 'status': g.get('status', ''),
                'mobile_otp_enabled': g.get('mobile_otp_enabled', False),
                'push_enabled': g.get('push_enabled', False), 'sms_enabled': g.get('sms_enabled', False),
                'voice_enabled': g.get('voice_enabled', False), 'duo_group_id': gid
            }, keep_empty=True)
            self.nodes.append(self.create_node(gid, "DuoGroup", name, props))
            for uid in self.get_group_users(gid):
                self.edges.append(self.create_edge(uid, gid, "DuoMemberOf"))

    def process_applications(self, apps: List[Dict[str, Any]]) -> None:
        self.logger.info("Processing applications...")
        for a in apps:
            aid = a.get('application_id', '') or a.get('integration_key', '')
            name = a.get('name', '')
            if not aid or not name:
                continue
            props = self._clean({
                'integration_key': a.get('integration_key', ''), 'type': a.get('type', ''),
                'notes': a.get('notes', ''), 'username_normalization_policy': a.get('username_normalization_policy', ''),
                'duo_application_id': aid
            }, keep_empty=True)
            self.nodes.append(self.create_node(aid, "DuoApplication", name, props))

    def process_admins(self, admins: List[Dict[str, Any]]) -> None:
        self.logger.info("Processing administrators...")
        for a in admins:
            aid = a.get('admin_id', '')
            if not aid:
                continue
            name = a.get('name', '') or a.get('email', '') or aid
            props = self._clean({
                'email': a.get('email', ''), 'phone': a.get('phone', ''), 'role': a.get('role', ''),
                'status': a.get('status', ''), 'created': a.get('created', ''), 'last_login': a.get('last_login'),
                'password_change_required': a.get('password_change_required', False), 'duo_admin_id': aid
            }, keep_empty=True)
            self.nodes.append(self.create_node(aid, "DuoAdmin", name, props))

    def process_phones(self, phones: List[Dict[str, Any]]) -> None:
        self.logger.info("Processing phones...")
        for p in phones:
            pid = p.get('phone_id', '')
            if not pid:
                continue
            name = p.get('name', '') or p.get('number', '') or pid
            caps = p.get('capabilities', [])
            props = self._clean({
                'number': p.get('number', ''), 'name': p.get('name', ''), 'activated': p.get('activated', False),
                'type': p.get('type', ''), 'platform': p.get('platform', ''), 'model': p.get('model', ''),
                'os_version': p.get('os_version', ''), 'app_version': p.get('app_version', ''),
                'capabilities': ','.join(caps) if caps else '', 'extension': p.get('extension', ''),
                'predelay': p.get('predelay', ''), 'postdelay': p.get('postdelay', ''),
                'encrypted': p.get('encrypted', ''), 'fingerprint': p.get('fingerprint', ''),
                'screenlock': p.get('screenlock', ''), 'tampered': p.get('tampered', ''),
                'last_seen': p.get('last_seen', ''), 'sms_passcodes_sent': p.get('sms_passcodes_sent', False),
                'duo_phone_id': pid
            })
            self.nodes.append(self.create_node(pid, "DuoPhone", name, props))
            for u in p.get('users', []):
                if u.get('user_id'):
                    self.edges.append(self.create_edge(u['user_id'], pid, "DuoHasMFADevice"))

    # --- Relationships ---

    def _get_account_id(self) -> str:
        host = self.admin_api.host
        clean = host[4:] if host.startswith('api-') else host
        return f"duo_account_{clean}"

    def create_duo_account_node(self) -> None:
        self.logger.info("Creating Duo Account node...")
        host = self.admin_api.host
        clean = host[4:] if host.startswith('api-') else host
        self.nodes.append(self.create_node(self._get_account_id(), "DuoAccount",
                                           f"Duo Account ({clean})", {'api_hostname': host, 'account_type': 'duo_tenant'}))

    def create_duo_account_relationships(self, users: List[Dict], admins: List[Dict],
                                          groups: List[Dict], apps: List[Dict]) -> None:
        self.logger.info("Creating Duo Account relationships...")
        acct = self._get_account_id()
        mfa_kinds = {'DuoPhone', 'DuoToken', 'DuoWebAuthnCredential'}

        for u in users:
            if u.get('user_id'):
                self.edges.append(self.create_edge(acct, u['user_id'], "DuoContains"))
        for a in admins:
            if a.get('admin_id'):
                self.edges.append(self.create_edge(acct, a['admin_id'], "DuoContains"))
                self.edges.append(self.create_edge(a['admin_id'], acct, "DuoAdminTo"))
        for g in groups:
            if g.get('group_id'):
                self.edges.append(self.create_edge(acct, g['group_id'], "DuoContains"))
        for ap in apps:
            aid = ap.get('application_id', '') or ap.get('integration_key', '')
            if aid:
                self.edges.append(self.create_edge(acct, aid, "DuoContains"))

        mfa_count = 0
        for n in self.nodes:
            if set(n.get('kinds', [])) & mfa_kinds:
                self.edges.append(self.create_edge(acct, n['id'], "DuoContains"))
                mfa_count += 1
        self.logger.info(f"Created DuoContains edges to {mfa_count} MFA devices")

    def create_application_access_edges(self, users: List[Dict], apps: List[Dict]) -> None:
        self.logger.info("Creating application access relationships...")
        user_lookup = {u['user_id']: u for u in users if u.get('user_id')}
        group_lookup = {}
        for n in self.nodes:
            if n.get('kinds') == ['DuoGroup']:
                group_lookup[n['id']] = n
                if n['properties'].get('name'):
                    group_lookup[n['properties']['name']] = n

        for app in apps:
            aid = app.get('application_id', '') or app.get('integration_key', '')
            if not aid:
                continue
            aname = app.get('name', aid)

            if app.get('user_access'):
                self._create_user_edges(app['user_access'], aid, aname, user_lookup)

            for field in self.GROUP_FIELDS:
                if app.get(field):
                    self._create_group_edges(app[field], aid, group_lookup, field)
                    break

    def _create_all_users_edges(self, aid: str, aname: str, user_lookup: Dict) -> None:
        self.logger.info(f"Creating access edges for ALL_USERS to app {aname}")
        for u in user_lookup.values():
            if u.get('status') in ['active', 'bypass']:
                self.edges.append(self.create_edge(u['user_id'], aid, "DuoHasAccessTo", {'access_type': 'all_users'}))

    def _create_user_edges(self, access: Any, aid: str, aname: str, user_lookup: Dict) -> None:
        if isinstance(access, str):
            if self._should_skip(access):
                return
            if access.upper() == 'ALL_USERS':
                return self._create_all_users_edges(aid, aname, user_lookup)
            access = [access]

        for entry in access:
            if isinstance(entry, str):
                if self._should_skip(entry):
                    continue
                if entry.upper() == 'ALL_USERS':
                    self._create_all_users_edges(aid, aname, user_lookup)
                    continue
                uid, props = entry, None
            elif isinstance(entry, dict):
                uid = entry.get('user_id')
                props = self._clean({'access_granted': entry.get('access_granted'),
                                     'access_type': entry.get('access_type', 'unknown')}) or None
            else:
                continue

            if uid and len(uid) >= 2 and uid in user_lookup:
                self.edges.append(self.create_edge(uid, aid, "DuoHasAccessTo", props))

    def _create_group_edges(self, groups: Any, aid: str, group_lookup: Dict, field: str) -> None:
        if isinstance(groups, str):
            if self._should_skip(groups):
                return
            groups = [groups]

        for entry in groups:
            if isinstance(entry, str):
                if self._should_skip(entry):
                    continue
                gid, props = entry, {'permission_source': field}
            elif isinstance(entry, dict):
                gid = entry.get('group_id') or entry.get('name') or entry.get('group_name')
                props = self._clean({'access_granted': entry.get('access_granted'),
                                     'access_type': entry.get('access_type', 'group_permission'),
                                     'permission_source': field})
            else:
                continue

            if not gid or len(gid) < 2:
                continue

            node = group_lookup.get(gid)
            if not node:
                self.logger.warning(f"Group {gid} not found for app {aid}")
                continue

            self.edges.append(self.create_edge(node['id'], aid, "DuoHasAccessTo", props))

    # --- Export ---

    def generate_bloodhound_data(self) -> Dict[str, Any]:
        self.logger.info("Fetching all data from Duo...")
        users = self.get_all_users()
        groups = self.get_all_groups()
        apps = self.get_all_applications()
        admins = self.get_all_admins()
        phones = self.get_all_phones()

        self.process_users(users)
        self.process_groups(groups)
        self.process_applications(apps)
        self.process_admins(admins)
        self.process_phones(phones)
        self.create_duo_account_node()
        self.create_application_access_edges(users, apps)
        self.create_duo_account_relationships(users, admins, groups, apps)

        self.logger.info(f"Generated {len(self.nodes)} nodes and {len(self.edges)} edges")
        return {"metadata": {"source_kind": "DuoBase"}, "graph": {"nodes": self.nodes, "edges": self.edges}}

    def export_to_file(self, filename: str) -> None:
        data = self.generate_bloodhound_data()
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Data exported to {filename}")


def main():
    parser = argparse.ArgumentParser(description='DuoHound - Convert Duo Admin API data to BloodHound format')
    parser.add_argument('--integration-key', required=True, help='Duo integration key')
    parser.add_argument('--secret-key', required=True, help='Duo secret key')
    parser.add_argument('--api-hostname', required=True, help='Duo API hostname')
    parser.add_argument('--output', '-o', default='duo_bloodhound_data.json', help='Output filename')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    parser.add_argument('--skip-mfa-details', action='store_true', help='Skip detailed MFA device queries (faster)')
    parser.add_argument('--version', action='version', version=f'DuoHound {__version__}')
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        converter = DuoToBloodHoundConverter(args.integration_key, args.secret_key,
                                              args.api_hostname, args.skip_mfa_details)
        converter.export_to_file(args.output)
        print(f"Successfully exported Duo data to {args.output}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
