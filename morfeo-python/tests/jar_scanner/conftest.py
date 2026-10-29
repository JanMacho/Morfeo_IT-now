"""Compila los fixtures Java de tests/fixtures/java y monta los jars de prueba.

Se compila una vez por sesión con ``javac --release 21``. Los jars imitan los
layouts reales: fat jar de Spring Boot (``BOOT-INF/classes`` y un jar anidado en
``BOOT-INF/lib``), war y jar plano. Si no hay JDK, los tests se saltan.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "java"
BACKEND_JAR = Path(__file__).resolve().parents[3] / "backend-java" / "target" / "bank-api-stress.jar"

HAS_JDK = shutil.which("javac") is not None


def _sources(root: Path) -> list[str]:
    return sorted(str(p) for p in root.rglob("*.java"))


def _javac(out: Path, sources: list[str], *flags: str, classpath: Path | None = None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["javac", "--release", "21", "-encoding", "UTF-8", "-d", str(out), *flags]
    if classpath is not None:
        cmd += ["-cp", str(classpath)]
    subprocess.run(cmd + sources, check=True, capture_output=True, text=True)
    return out


def _zip(target: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            z.writestr(info, entries[name])
    return target


def _tree(root: Path, prefix: str) -> dict[str, bytes]:
    return {prefix + p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _manifest(**attrs: str) -> bytes:
    lines = ["Manifest-Version: 1.0"] + [f"{k.replace('_', '-')}: {v}" for k, v in attrs.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


@dataclass(frozen=True)
class Jars:
    stubs_classes: Path
    spring_classes: Path
    classfile_classes: Path
    spring_boot: Path  # -parameters y -g, sin context-path
    spring_boot_lvt: Path  # solo -g: nombres desde LocalVariableTable
    spring_boot_bare: Path  # -g:none: sin nombres de parámetros
    spring_boot_context: Path  # server.servlet.context-path=/bank
    war: Path  # application.yml con context-path y servlet path
    javalin: Path


@pytest.fixture(scope="session")
def jars(tmp_path_factory: pytest.TempPathFactory) -> Jars:
    if not HAS_JDK:
        pytest.skip("se necesita javac (JDK 21) para compilar los fixtures")
    tmp = tmp_path_factory.mktemp("java")
    stubs = _javac(tmp / "stubs", _sources(FIXTURES / "stubs"))
    stubs_jar = _zip(tmp / "stubs.jar", _tree(stubs, ""))
    spring_src = _sources(FIXTURES / "springapp")
    variants = {
        "full": _javac(tmp / "spring-full", spring_src, "-parameters", "-g", classpath=stubs),
        "lvt": _javac(tmp / "spring-lvt", spring_src, "-g", classpath=stubs),
        "bare": _javac(tmp / "spring-bare", spring_src, "-g:none", classpath=stubs),
    }
    cf = _javac(tmp / "classfile", _sources(FIXTURES / "classfile"), "-g")
    javalin_classes = _javac(tmp / "javalin", _sources(FIXTURES / "javalinapp"), "-g", classpath=stubs)
    resources = FIXTURES / "resources"

    def boot(name: str, classes: Path, props: Path) -> Path:
        entries = _tree(classes, "BOOT-INF/classes/")
        entries["BOOT-INF/classes/application.properties"] = props.read_bytes()
        entries["BOOT-INF/lib/stubs.jar"] = stubs_jar.read_bytes()
        entries["META-INF/MANIFEST.MF"] = _manifest(
            Main_Class="org.springframework.boot.loader.launch.JarLauncher",
            Start_Class="com.example.bank.BankApplication",
        )
        return _zip(tmp / name, entries)

    base = resources / "base" / "application.properties"
    war_entries = _tree(variants["full"], "WEB-INF/classes/")
    war_entries["WEB-INF/classes/application.yml"] = (resources / "war" / "application.yml").read_bytes()
    war_entries["WEB-INF/lib/stubs.jar"] = stubs_jar.read_bytes()
    javalin_entries = _tree(javalin_classes, "") | _tree(stubs, "")
    javalin_entries["META-INF/MANIFEST.MF"] = _manifest(Main_Class="com.example.shop.ShopApp")
    return Jars(
        stubs_classes=stubs,
        spring_classes=variants["full"],
        classfile_classes=cf,
        spring_boot=boot("bank.jar", variants["full"], base),
        spring_boot_lvt=boot("bank-lvt.jar", variants["lvt"], base),
        spring_boot_bare=boot("bank-bare.jar", variants["bare"], base),
        spring_boot_context=boot("bank-context.jar", variants["full"], resources / "context-path" / "application.properties"),
        war=_zip(tmp / "shop.war", war_entries),
        javalin=_zip(tmp / "shop.jar", javalin_entries),
    )
