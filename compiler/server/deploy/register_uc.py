"""SVC-COMPILER-UC — deploy our compiler artifacts and UC function; no grants or App changes."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path

MARKER = 'SVC-COMPILER-UC: deployment-owned CQL compiler'


def registration_statement(function: tuple[str, str, str], dependencies: list[str]) -> str:
    """SVC-COMPILER-UC — explicit environment/JAR dependencies, not workspace-specific settings."""
    if (len(function) != 3 or any(not value.strip() for value in function)
            or not dependencies or any(not path.startswith('/Volumes/') for path in dependencies)):
        raise ValueError('compiler registration requires a full function name and Volume JARs')
    name = '.'.join('`' + value.replace('`', '``') + '`' for value in function)
    jars = json.dumps(dependencies).replace("'", "''")
    return f'''CREATE OR REPLACE FUNCTION {name}(operation STRING, request_gzip_base64 STRING)
RETURNS STRING LANGUAGE SCALA NOT DETERMINISTIC
COMMENT '{MARKER}'
ENVIRONMENT (java_dependencies = '{jars}', environment_version = '4')
HANDLER 'com.databricks.cql.platform.compiler.UcCompiler.invoke' '''


def deploy(client, run, *, catalog: str, schema: str, volume: str, libraries: Path) -> dict:
    """SVC-COMPILER-UC — immutable content-addressed uploads; refuse unrelated existing functions."""
    from databricks.sdk.errors import NotFound
    from cql_databricks.compiler.uc import UcFunctionProvider

    function = (catalog, schema, 'cql_compile')
    try:
        existing = client.functions.get('.'.join(function))
    except NotFound:
        existing = None
    if existing is not None and existing.comment != MARKER:
        raise ValueError('refusing to replace a compiler function without our deployment marker')
    files = sorted(libraries.glob('*.jar'))
    if not files or not any(path.name.startswith('cql-databricks-compiler-server-') for path in files):
        raise ValueError('build :compiler:server:installDist before registering the compiler')

    def upload(path: Path) -> str:
        digest = sha256(path.read_bytes()).hexdigest()
        destination = f'/Volumes/{catalog}/{schema}/{volume}/compiler/{digest}/{path.name}'
        try:
            client.files.get_metadata(destination)
        except NotFound:
            client.files.create_directory(destination.rsplit('/', 1)[0])
            client.files.upload_from(destination, str(path), overwrite=False, use_parallel=True)
        downloaded = client.files.download(destination).contents
        checksum = sha256()
        try:
            for chunk in iter(lambda: downloaded.read(1024 * 1024), b''):
                checksum.update(chunk)
        finally:
            downloaded.close()
        if checksum.hexdigest() != digest:
            raise ValueError(f'compiler artifact checksum mismatch: {path.name}')
        return destination

    with ThreadPoolExecutor(max_workers=6) as pool:
        dependencies = list(pool.map(upload, files))
    run(registration_statement(function, dependencies))
    capabilities = UcFunctionProvider(run, function).capabilities()
    if not capabilities.get('compilerVersion', '').startswith('uc-sha256-'):
        raise ValueError('registered compiler did not report its artifact identity')
    return {'function': '.'.join(function), 'jars': len(dependencies),
            'compilerVersion': capabilities['compilerVersion']}


def main() -> None:
    """SVC-LOCAL-CONFIG-BUNDLE — all workspace settings come from the resolved deployment bundle."""
    from databricks.sdk import WorkspaceClient
    from cql_databricks.data_bindings import warehouse_runner

    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument('--profile', required=True)
    command.add_argument('--bundle-json', required=True)
    command.add_argument('--libraries', type=Path, required=True)
    args = command.parse_args()
    bundle = json.loads(args.bundle_json)

    def setting(name):
        value = bundle['variables'][name]['value']
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{name} must be configured in databricks.local.yml')
        return value

    client = WorkspaceClient(profile=args.profile, host=bundle['workspace']['host'])
    result = deploy(client, warehouse_runner(client, setting('warehouse_id')),
                    catalog=setting('uc_catalog'), schema=setting('uc_schema'),
                    volume=setting('runtime_volume'), libraries=args.libraries)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
