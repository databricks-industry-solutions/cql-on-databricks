#!/usr/bin/env python3
"""Bootstrap launcher for the CQL on Databricks Compiler Service on a Databricks App (SVC-PACKAGING, SVC-COMPILE-ENDPOINT).

A Databricks App runs Python (not Java), and its source is Workspace files with a 10 MB/file limit. Our
OWN jars (compiler / -service, the accelerator jar) are all < 10 MB, so they ship as normal app
source alongside every other small dependency. Only THREE jars exceed 10 MB — and all three are PUBLIC
Maven artifacts (icu4j, spark-connect-client-jvm, scala-compiler). Plus a portable Temurin JRE (linux-x64).

Where those oversized artifacts come from (SVC-PACKAGING): the deploy stages them into the runtime UC Volume
(`scripts/deploy/deploy.sh`), and this launcher reads them FROM THE VOLUME via the Databricks SDK — authenticated
as the ambient App identity, authorized by the App's `uc_securable` READ_VOLUME grant (databricks.yml). That
removes the public-internet egress dependency at cold start. When the volume is configured it is the only
source: a failed read stops the boot with the reason. Only an App with no volume configured (a local run) reads
the public URLs. Then `exec` the JVM with a classpath of the app-source lib/ PLUS the fetched jars. Spark-free at
runtime (EXEC-SQL-BACKEND): compiles CQL/ELM -> SQL text, holds no SparkSession.

Keep MAVEN_JARS in sync with the versions installDist actually resolves for sparkVersion=4.1.2 (Spark
4.1.2 pulls icu4j 77.1 + scala-compiler 2.13.17 transitively). stage-compiler.sh removes exactly these
from the app-source lib/ (verify with `ls build/install/.../lib/` after a toolchain bump). deploy.sh reads the artifact
manifest from THIS file (`python bootstrap.py --manifest`), so the list lives in one place.
"""
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request

JRE_URL = os.environ.get(
    "CQL_DATABRICKS_JRE_URL",
    "https://github.com/adoptium/temurin21-binaries/releases/download/"
    "jdk-21.0.5%2B11/OpenJDK21U-jre_x64_linux_hotspot_21.0.5_11.tar.gz",
)
# Maven base for the 3 oversized PUBLIC jars. Default: Maven Central, a public host the App can
# reach. Repoint with
# CQL_DATABRICKS_MAVEN_URL to an in-network mirror if public egress is locked down.
MAVEN_URL = os.environ.get("CQL_DATABRICKS_MAVEN_URL", "https://repo1.maven.org/maven2").rstrip("/")
# (group-path, artifact, version) -> <base>/<group-path>/<artifact>/<version>/<artifact>-<version>.jar
MAVEN_JARS = [
    ("com/ibm/icu", "icu4j", "77.1"),
    ("org/apache/spark", "spark-connect-client-jvm_2.13", "4.1.2"),
    ("org/scala-lang", "scala-compiler", "2.13.17"),
]
MAIN_CLASS = "com.databricks.cql.platform.compiler.CompilerServiceApp"
# Spark-on-JDK opens (build.gradle applicationDefaultJvmArgs). Inert while Spark-free, kept for safety.
JVM_OPENS = [f"--add-opens=java.base/{p}=ALL-UNNAMED"
             for p in ("java.nio", "sun.nio.ch", "java.lang", "java.util", "java.lang.invoke")]
JRE_DIR = "/tmp/cql-jre"
EXTRA_LIB = "/tmp/cql-extra-lib"

# The runtime UC Volume the deploy stages artifacts into (databricks.yml var; app.yaml passes the concrete
# path). Unset / still-templated (`__...__`) => volume disabled, use the public URLs. The relpaths here are
# also what deploy.sh uploads to (see `--manifest`), so the two sides never drift.
_vol = os.environ.get("CQL_DATABRICKS_ARTIFACT_VOLUME", "")
ARTIFACT_VOLUME = "" if _vol.startswith("__") else _vol.rstrip("/")
_JRE_BASENAME = JRE_URL.rsplit("/", 1)[-1].replace("%2B", "+")


def artifact_manifest():
    """The full artifact set as ``(public_url, volume_relpath)`` pairs. The single source of truth for
    BOTH this launcher's fetches and what `scripts/deploy/deploy.sh` uploads to the volume."""
    items = [(JRE_URL, f"jre/{_JRE_BASENAME}")]
    for group_path, artifact, version in MAVEN_JARS:
        jar = f"{artifact}-{version}.jar"
        items.append((f"{MAVEN_URL}/{group_path}/{artifact}/{version}/{jar}", f"maven/{jar}"))
    return items


class ArtifactReadError(RuntimeError):
    """An artifact could not be read from the runtime volume that the App is configured to use."""


def _fetch(dest: str, volume_relpath: str, url: str) -> None:
    """Fetch one artifact to ``dest``. With a runtime volume configured, the volume (via the Databricks SDK) is
    the only source, and a failed read raises :class:`ArtifactReadError` so the App does not boot. With no volume
    configured, the public ``url`` is used (SVC-PACKAGING)."""
    if not ARTIFACT_VOLUME:
        print(f"[bootstrap] downloading {volume_relpath} from {url}", flush=True)
        urllib.request.urlretrieve(url, dest)
        return
    vpath = f"{ARTIFACT_VOLUME}/{volume_relpath}"
    print(f"[bootstrap] downloading {volume_relpath} from volume {vpath}", flush=True)
    try:
        from databricks.sdk import WorkspaceClient  # lazy: only needed on the volume path
        resp = WorkspaceClient().files.download(vpath)
        with open(dest, "wb") as f:
            shutil.copyfileobj(resp.contents, f)
    except Exception as e:
        raise ArtifactReadError(
            f"could not read {vpath} from the runtime volume: {e!r}. Check that the App's service principal "
            f"has READ_VOLUME on the volume (databricks.yml, uc_securable).") from e


def ensure_jre() -> str:
    if os.path.exists(os.path.join(JRE_DIR, "bin", "java")):
        print(f"[bootstrap] JRE cached at {JRE_DIR}", flush=True)
        return JRE_DIR
    tmp = "/tmp/cql-jre.tar.gz"
    _fetch(tmp, f"jre/{_JRE_BASENAME}", JRE_URL)
    ex = "/tmp/cql-jre-x"
    shutil.rmtree(ex, ignore_errors=True)
    os.makedirs(ex)
    with tarfile.open(tmp) as tf:
        try:
            tf.extractall(ex, filter="data")
        except TypeError:
            tf.extractall(ex)
    top = os.path.join(ex, os.listdir(ex)[0])   # Temurin tar has a single jdk-* top dir
    shutil.rmtree(JRE_DIR, ignore_errors=True)
    shutil.move(top, JRE_DIR)
    print(f"[bootstrap] JRE ready at {JRE_DIR}", flush=True)
    return JRE_DIR


def ensure_maven_jars() -> str:
    os.makedirs(EXTRA_LIB, exist_ok=True)
    for group_path, artifact, version in MAVEN_JARS:
        jar = f"{artifact}-{version}.jar"
        dest = os.path.join(EXTRA_LIB, jar)
        if os.path.exists(dest):
            continue
        url = f"{MAVEN_URL}/{group_path}/{artifact}/{version}/{jar}"
        _fetch(dest, f"maven/{jar}", url)
    print(f"[bootstrap] {len(MAVEN_JARS)} maven jars ready in {EXTRA_LIB}", flush=True)
    return EXTRA_LIB


def main() -> None:
    src = "volume " + ARTIFACT_VOLUME if ARTIFACT_VOLUME else "public URLs"
    print(f"[bootstrap] artifact source: {src}", flush=True)
    java_home = ensure_jre()
    extra = ensure_maven_jars()
    here = os.path.dirname(os.path.abspath(__file__))
    classpath = os.pathsep.join([os.path.join(here, "lib", "*"), os.path.join(extra, "*")])
    port = os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("PORT") or "8080"
    java = os.path.join(java_home, "bin", "java")
    cmd = [java, *JVM_OPENS, "-cp", classpath, MAIN_CLASS]
    env = dict(os.environ, JAVA_HOME=java_home, PORT=port)
    print(f"[bootstrap] DATABRICKS_APP_PORT={os.environ.get('DATABRICKS_APP_PORT')} -> binding {port}", flush=True)
    print(f"[bootstrap] exec {java} -cp <app lib/ + {len(MAVEN_JARS)} maven jars> {MAIN_CLASS}", flush=True)
    os.execve(java, cmd, env)  # replace this process so signals reach the JVM


if __name__ == "__main__":
    # `--manifest`: print `<public_url>\t<volume_relpath>` per artifact for scripts/deploy/deploy.sh (DRY: the
    # deploy uploads exactly what this launcher expects to read). No SDK / network needed to list.
    if "--manifest" in sys.argv[1:]:
        for url, rel in artifact_manifest():
            print(f"{url}\t{rel}")
    else:
        main()
