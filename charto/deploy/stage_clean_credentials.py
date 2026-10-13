#!/usr/bin/env python3
"""Provision fresh serving-only credentials without printing any secret.

Run from the trusted operator machine. Does not revoke shared credentials:
upstream jobs keep their current identities until their coordinated rotation.
"""
import json
import secrets
import subprocess
import urllib.parse

import psycopg2
from psycopg2 import sql

VAULT = 'pivot-clean-kv-india'
ROLE = 'pivotserve_20261008'


def az(*args):
    result = subprocess.run(['az', *args, '-o', 'json'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Azure operation failed: ' + ' '.join(args[:4]))
    return json.loads(result.stdout) if result.stdout.strip() else None


def get(name):
    return az('keyvault', 'secret', 'show', '--vault-name', 'pivot-kv-india', '--name', name)['value']


def put(name, value):
    az('keyvault', 'secret', 'set', '--vault-name', VAULT, '--name', name, '--value', value)


def main():
    dsns = {name: get(name) for name in ('database-url', 'financials-dsn', 'enrich-dsn')}
    password = secrets.token_urlsafe(48)
    con = psycopg2.connect(dsns['database-url'].replace('postgresql+psycopg2:', 'postgresql:'))
    con.autocommit = True
    with con.cursor() as cur:
        cur.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (ROLE,))
        if cur.fetchone():
            raise RuntimeError('Serving role exists; inspect before rerunning')
        cur.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD %s CONNECTION LIMIT 24').format(sql.Identifier(ROLE)), (password,))
    con.close()
    for name, dsn in dsns.items():
        con = psycopg2.connect(dsn.replace('postgresql+psycopg2:', 'postgresql:'))
        con.autocommit = True
        with con.cursor() as cur:
            cur.execute('SELECT current_database()')
            database = cur.fetchone()[0]
            cur.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database), sql.Identifier(ROLE)))
            cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema'")
            schemas = [r[0] for r in cur.fetchall() if r[0] != 'charto_landing']
            for schema in schemas:
                cur.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(ROLE)))
                permission = sql.SQL('SELECT, INSERT, UPDATE, DELETE') if name == 'database-url' else sql.SQL('SELECT')
                cur.execute(sql.SQL('GRANT {} ON ALL TABLES IN SCHEMA {} TO {}').format(permission, sql.Identifier(schema), sql.Identifier(ROLE)))
                if name == 'database-url':
                    cur.execute(sql.SQL('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(ROLE)))
        con.close()
        u = urllib.parse.urlsplit(dsn)
        netloc = f'{ROLE}:{urllib.parse.quote(password, safe="")}@{u.hostname}' + (f':{u.port}' if u.port else '')
        fresh = urllib.parse.urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))
        # Verify the new identity sees real data, not merely an empty DB.
        test = psycopg2.connect(fresh.replace('postgresql+psycopg2:', 'postgresql:'))
        with test.cursor() as cur:
            probe = {'database-url': 'public.instrument_master', 'financials-dsn': 'mc.companies', 'enrich-dsn': 'enrich.company_profile'}[name]
            cur.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(sql.Identifier(*probe.split('.'))))
            if not cur.fetchone():
                raise RuntimeError('Reference probe empty')
        test.close()
        put(name, fresh)
        print(name, 'FRESH_SERVING_ROLE_VERIFIED', flush=True)

    old_key = get('azure-key')
    keys = az('cognitiveservices', 'account', 'keys', 'list', '-g', 'PIVOT', '-n', 'deploymentpivot111')
    if old_key != keys['key1']:
        raise RuntimeError('Expected active model key slot changed; stop')
    fresh_keys = az('cognitiveservices', 'account', 'keys', 'regenerate', '-g', 'PIVOT', '-n', 'deploymentpivot111', '--key-name', 'key2')
    put('azure-key', fresh_keys['key2'])
    for name in ('azure-openai-endpoint', 'azure-openai-legacy-endpoint', 'azure-project-endpoint',
                 'kite-api-key', 'kite-api-secret', 'kite-token-enc-key', 'google-client-id'):
        put(name, get(name))
    put('jwt-secret-key', secrets.token_urlsafe(64))
    put('redis-url', 'redis://127.0.0.1:6379/0')
    print('MODEL_KEY2_REFRESHED; JWT_FRESH; KITE_TEMPORARY_AS_APPROVED', flush=True)


if __name__ == '__main__':
    main()
