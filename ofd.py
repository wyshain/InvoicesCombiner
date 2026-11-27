"""Render the first OFD page locally for the three-up print layout."""
import os
import shutil
import stat
import subprocess
import zipfile
import zlib
from io import BytesIO
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from xml.etree import ElementTree
import pymupdf

JAR = Path(__file__).resolve().parent / "ofd" / "target" / "ofd-print.jar"
MAX_UNPACKED = 256 * 1024 * 1024


class OfdInputError(ValueError):
    pass


class OfdUnavailable(RuntimeError):
    pass


class OfdTreeBuilder(ElementTree.TreeBuilder):
    def doctype(self, name, public_id, system_id):
        raise OfdInputError("OFD 的 XML 含有不支持的文档类型声明。")


def prepare_fonts(root: Path, temp: Path) -> None:
    fallback = temp / "print-fallback.ttf"
    for path in root.rglob("*"):
        if path.suffix.lower() != ".xml" or not path.is_file():
            continue
        tree = ElementTree.parse(path, parser=ElementTree.XMLParser(target=OfdTreeBuilder()))
        resource = tree.getroot()
        if resource.tag.split("}")[-1] != "Res":
            continue
        changed = False
        base_loc = resource.get("BaseLoc", "").replace("\\", "/")
        base = root / base_loc.lstrip("/") if base_loc.startswith("/") else path.parent / base_loc
        for font in resource.findall(".//{*}Fonts/{*}Font"):
            file_node = font.find("{*}FontFile")
            if file_node is not None:
                loc = (file_node.text or "").strip().split("#")[0].replace("\\", "/")
                embedded = root / loc.lstrip("/") if loc.startswith("/") else base / loc
                if embedded.resolve().is_relative_to(root) and embedded.is_file():
                    continue
                font.remove(file_node)
            if not fallback.exists():
                fallback.write_bytes(pymupdf.Font("cjk").buffer)
            font.set("FamilyName", str(fallback))
            changed = True
        if changed:
            tree.write(path, encoding="utf-8", xml_declaration=True)


def unpack(data: bytes, root: Path) -> None:
    with zipfile.ZipFile(BytesIO(data)) as package:
        entries = package.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > MAX_UNPACKED:
            raise OfdInputError("OFD 解包后过大。")
        if "OFD.xml" not in package.namelist():
            raise OfdInputError("缺少 OFD.xml 文档入口，请检查文件。")
        if "Encryptions.xml" in package.namelist() or any(e.flag_bits & 1 for e in entries):
            raise OfdInputError("请先解除 OFD 的打开密码。")

        for entry in entries:
            path = PurePosixPath(entry.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or stat.S_ISLNK(entry.external_attr >> 16):
                raise OfdInputError("OFD 内部文件路径无效。")
            dest = root.joinpath(*path.parts)
            if entry.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as src, dest.open("xb") as dst:
                shutil.copyfileobj(src, dst)


def convert_first_page(data: bytes) -> bytes:
    java = os.environ.get("INVOICES_JAVA") or shutil.which("java")
    if not java or not JAR.is_file():
        raise OfdUnavailable("本机 OFD 转换器尚未就绪。")
    with TemporaryDirectory(prefix="invoice-ofd-") as tmp:
        folder = Path(tmp).resolve()
        root = folder / "document"
        root.mkdir()
        try:
            unpack(data, root)
            prepare_fonts(root, folder)
        except (zipfile.BadZipFile, NotImplementedError, ElementTree.ParseError, FileExistsError,
                IsADirectoryError, NotADirectoryError, EOFError, zlib.error) as error:
            raise OfdInputError("无法读取 OFD 文档包，请检查文件是否完整。") from error
        output = folder / "page.pdf"
        policy = JAR.parent.parent / "print.policy"
        args = [java, "-Xmx512m", "-Djava.awt.headless=true", f"-Djava.io.tmpdir={folder}",
                f"-Duser.home={folder}", "-Djava.security.manager", f"-Djava.security.policy=={policy.as_uri()}",
                "-Djavax.xml.accessExternalDTD=", "-Djavax.xml.accessExternalSchema=",
                "-Djavax.xml.accessExternalStylesheet=", "-jar", str(JAR), str(root), str(output)]
        diagnostics = folder / "converter.log"
        try:
            with diagnostics.open("wb") as log:
                result = subprocess.run(args, cwd=folder, stdin=subprocess.DEVNULL,
                                        env={"PATH": os.defpath, "LANG": "en_US.UTF-8"},
                                        stdout=subprocess.DEVNULL, stderr=log, timeout=60)
        except subprocess.TimeoutExpired as error:
            raise OfdInputError("OFD 首页转换超时。") from error
        except OSError as error:
            raise OfdUnavailable("无法启动本机 OFD 转换器。") from error
        if result.returncode or not output.is_file() or output.stat().st_size == 0:
            with diagnostics.open("rb") as log:
                message = log.read(8192)
            if any(marker in message for marker in (b"UnsupportedClassVersionError", b"Java Runtime",
                    b"Security Manager is not supported", b"Security Manager is not allowed",
                    b"Security Manager is not supported or allowed", b"Invalid or corrupt jarfile")):
                raise OfdUnavailable("请为本地 OFD 转换器配置 Java 17 或 21。")
            raise OfdInputError("OFD 首页转换失败，请确认文档可正常打开。")
        with pymupdf.open(output) as page_pdf:
            page_pdf.subset_fonts()
            return page_pdf.tobytes(garbage=4, deflate=True, use_objstms=1)
